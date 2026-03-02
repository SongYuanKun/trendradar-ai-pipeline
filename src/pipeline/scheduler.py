"""定时任务封装：单次 pipeline 执行 + APScheduler。"""
import logging
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from .ai_analyzer import AIAnalyzer
from .config_loader import load_config
from .downstream import build_payload, load_retry_queue, send_to_downstream
from .mcp_client import fetch_latest_news

logger = logging.getLogger(__name__)


def _url_for_log(url: str) -> str:
    """脱敏：仅保留 scheme + host + port。"""
    if not url or not url.strip():
        return "(未配置)"
    try:
        p = urlparse(url.strip())
        netloc = p.netloc or (p.path.split("/")[0] if p.path else "") or "(empty)"
        return f"{p.scheme or 'http'}://{netloc}"
    except Exception:
        return "(解析失败)"


async def run_pipeline_once(config: Optional[Dict[str, Any]] = None) -> None:
    """
    执行一次完整管道：拉取 MCP 数据 → 百炼分析 → 投递下游；
    并尝试重试队列中的历史失败批次。
    """
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

    # 1) 重试队列
    retries = load_retry_queue()
    logger.info("待重试 batch 数: %d", len(retries))
    retry_ok, retry_fail = 0, 0
    for payload in retries:
        ok = await send_to_downstream(
            down_cfg.get("url", ""),
            payload,
            timeout=down_cfg.get("timeout", 30),
            api_key=down_cfg.get("api_key"),
        )
        if ok:
            retry_ok += 1
            logger.info("重试队列投递成功 batch_id=%s", payload.batch_id)
        else:
            retry_fail += 1
    if retries:
        logger.info("重试队列处理完毕，成功 %d，失败 %d", retry_ok, retry_fail)

    # 2) 拉取 MCP
    try:
        news_list = await fetch_latest_news(
            base_url=base_url,
            limit=limit,
            include_url=include_url,
            timeout=60.0,
        )
    except Exception as e:
        logger.exception("MCP 拉取失败，本次任务终止: %s", e)
        return

    first_title = ""
    if news_list:
        t = news_list[0].get("title") or ""
        first_title = (t[:30] + "…") if len(t) > 30 else t
    logger.info("MCP 拉取成功，条数=%d，首条标题摘要: %s", len(news_list), first_title or "(无)")

    if not news_list:
        logger.info("MCP 无新数据，本次跳过分析")
        return

    # 仅取第一条进行分析（拉取 50 条，分析 1 条）
    news_for_analysis = news_list[:1]
    logger.info("仅取首条进行分析，本次分析条数=1")

    # 3) 百炼分析
    batch_size = ai_cfg.get("batch_size", 10)
    mode = "批量" if batch_size and batch_size > 0 else "单条"
    logger.info("开始百炼分析，条数=%d，模式=%s(batch_size=%s)", len(news_for_analysis), mode, batch_size or "N/A")
    t0 = time.perf_counter()
    analyzer = AIAnalyzer(
        api_base=ai_cfg.get("api_base", ""),
        model=ai_cfg.get("model", "qwen-plus"),
        api_key=ai_cfg.get("api_key", ""),
        timeout=ai_cfg.get("timeout", 120),
        max_retries=ai_cfg.get("max_retries", 3),
        batch_size=batch_size,
    )
    analyses = await analyzer.analyze_batch(news_for_analysis)
    elapsed = time.perf_counter() - t0
    logger.info("百炼分析完成，条数=%d，耗时=%.2fs", len(analyses), elapsed)

    # 4) 投递下游
    payload = build_payload(analyses, sources=news_for_analysis)
    down_url = down_cfg.get("url")
    if not down_url:
        logger.warning("未配置 downstream.url，跳过投递")
        return
    logger.info("即将投递下游 url=%s items=%d batch_id=%s", _url_for_log(down_url), len(payload.items), payload.batch_id)
    await send_to_downstream(
        down_url,
        payload,
        timeout=down_cfg.get("timeout", 30),
        api_key=down_cfg.get("api_key"),
    )


def create_scheduler(config: Optional[Dict[str, Any]] = None) -> AsyncIOScheduler:
    """根据配置创建 AsyncIOScheduler，已添加 pipeline 任务。"""
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
