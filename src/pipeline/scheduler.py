"""定时任务封装：单次 pipeline 执行 + APScheduler。"""
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .ai_analyzer import AIAnalyzer
from .config_loader import load_config
from .downstream import load_retry_queue, send_to_downstream
from .mcp_client import fetch_latest_news

logger = logging.getLogger(__name__)


def _url_for_log(url: str) -> str:
    if not url or not url.strip():
        return "(未配置)"
    try:
        p = urlparse(url.strip())
        netloc = p.netloc or "(empty)"
        return f"{p.scheme or 'http'}://{netloc}"
    except Exception:
        return "(解析失败)"


async def run_pipeline_once(config: Optional[Dict[str, Any]] = None) -> None:
    """执行一次完整管道：拉取 MCP → 百炼分析 → 发布到小红书。"""
    if config is None:
        config = load_config()
    run_id = uuid.uuid4().hex[:8]
    logger.info("pipeline run started, run_id=%s", run_id)

    mcp_cfg = config.get("mcp") or {}
    ai_cfg = config.get("ai") or {}
    down_cfg = config.get("downstream") or {}

    base_url = mcp_cfg.get("base_url", "http://127.0.0.1:3333/mcp")
    tool_cfg = mcp_cfg.get("tools") or {}
    news_cfg = tool_cfg.get("get_latest_news") or {}
    limit = news_cfg.get("limit", 50)
    include_url = news_cfg.get("include_url", True)

    down_mcp_url = down_cfg.get("url", "")
    down_proxy_base = down_cfg.get("proxy_base")
    down_proxy_auth = down_cfg.get("proxy_auth")
    down_api_token = down_cfg.get("api_token")

    # 1) 重试队列
    retries = load_retry_queue()
    if retries:
        logger.info("待重试 %d 条", len(retries))
        retry_ok = 0
        for params in retries:
            # 重试队列里存的是原始 publish params，需要从中提取
            from .models import AnalysisItem
            analysis = AnalysisItem(
                key_points=params.get("key_points", []),
                sentiment=params.get("sentiment", "neutral"),
                topic=params.get("topic", ""),
                summary=params.get("summary", ""),
            )
            ok = await send_to_downstream(down_mcp_url, analysis)
            if ok:
                retry_ok += 1
        logger.info("重试完成，成功 %d/%d", retry_ok, len(retries))
        # 清理成功的重试文件
        if retry_ok == len(retries):
            retry_dir = Path("retry_queue")
            if retry_dir.is_dir():
                for f in retry_dir.glob("*.json"):
                    f.unlink(missing_ok=True)

    # 2) 拉取 MCP
    try:
        news_list = await fetch_latest_news(
            base_url=base_url,
            limit=limit,
            include_url=include_url,
            timeout=60.0,
        )
    except Exception as e:
        logger.exception("MCP 拉取失败，本次终止: %s", e)
        return

    first_title = ""
    if news_list:
        t = news_list[0].get("title") or ""
        first_title = (t[:30] + "…") if len(t) > 30 else t
    logger.info("MCP 拉取 %d 条，首条: %s", len(news_list), first_title or "(无)")

    if not news_list:
        logger.info("无新数据，跳过")
        return

    # 仅取第一条分析和发布
    news_for_analysis = news_list[:1]
    logger.info("取首条进行分析")

    # 3) 百炼分析
    t0 = time.perf_counter()
    analyzer = AIAnalyzer(
        api_base=ai_cfg.get("api_base", ""),
        model=ai_cfg.get("model", "qwen-plus"),
        api_key=ai_cfg.get("api_key", ""),
        timeout=ai_cfg.get("timeout", 120),
        max_retries=ai_cfg.get("max_retries", 3),
        batch_size=0,
        enable_search=ai_cfg.get("enable_search", True),
    )
    analyses = await analyzer.analyze_batch(news_for_analysis)
    elapsed = time.perf_counter() - t0
    logger.info("百炼分析完成，条数=%d，耗时=%.2fs", len(analyses), elapsed)

    # 4) 发布到小红书
    if not down_mcp_url:
        logger.warning("未配置 downstream.url，跳过发布")
        return

    for analysis, source in zip(analyses, news_for_analysis):
        if not analysis.summary and not analysis.key_points:
            logger.warning("分析结果为空，跳过发布: %s", source.get("title"))
            continue
        ok = await send_to_downstream(
            down_mcp_url, analysis, source=source,
            proxy_base=down_proxy_base, proxy_auth=down_proxy_auth,
            api_token=down_api_token,
        )
        if ok:
            logger.info("pipeline run_id=%s 发布成功", run_id)
        else:
            logger.error("pipeline run_id=%s 发布失败", run_id)


def create_scheduler(config: Optional[Dict[str, Any]] = None) -> AsyncIOScheduler:
    if config is None:
        config = load_config()
    sched_cfg = config.get("scheduler") or {}
    scheduler = AsyncIOScheduler()

    if sched_cfg.get("cron"):
        trigger = CronTrigger.from_crontab(sched_cfg["cron"])
    elif sched_cfg.get("interval_seconds"):
        trigger = IntervalTrigger(seconds=sched_cfg["interval_seconds"])
    else:
        trigger = IntervalTrigger(seconds=3600)

    scheduler.add_job(run_pipeline_once, trigger, id="pipeline", args=[config])
    return scheduler
