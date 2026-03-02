"""入口：加载配置、启动调度。"""
import asyncio
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

from .config_loader import load_config
from .scheduler import create_scheduler, run_pipeline_once


def _url_for_log(url: str) -> str:
    """脱敏：仅保留 scheme + host + port，便于排查。"""
    if not url or not url.strip():
        return "(未配置)"
    try:
        p = urlparse(url.strip())
        netloc = p.netloc or p.path.split("/")[0] or "(empty)"
        return f"{p.scheme or 'http'}://{netloc}"
    except Exception:
        return "(解析失败)"


def _setup_logging(config: dict) -> None:
    log_cfg = config.get("logging") or {}
    level = getattr(logging, (log_cfg.get("level") or "INFO").upper(), logging.INFO)
    path = log_cfg.get("path")
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(p, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers, force=True)


async def _run_forever() -> None:
    """在事件循环中保持运行，等待调度。"""
    while True:
        await asyncio.sleep(60)


def main() -> None:
    config = load_config()
    _setup_logging(config)

    logger = logging.getLogger("pipeline.main")
    logger.info("TrendRadar AI Pipeline 启动")

    mcp_cfg = config.get("mcp") or {}
    down_cfg = config.get("downstream") or {}
    ai_cfg = config.get("ai") or {}
    sched_cfg = config.get("scheduler") or {}
    mcp_url = _url_for_log(mcp_cfg.get("base_url", ""))
    down_url = _url_for_log(down_cfg.get("url", ""))
    ai_model = ai_cfg.get("model", "(未配置)")
    if sched_cfg.get("cron"):
        sched_desc = f"cron={sched_cfg['cron']}"
    else:
        sched_desc = f"interval_seconds={sched_cfg.get('interval_seconds', 3600)}"
    logger.info(
        "配置摘要: mcp.base_url=%s downstream.url=%s ai.model=%s scheduler=%s",
        mcp_url,
        down_url,
        ai_model,
        sched_desc,
    )

    scheduler = create_scheduler(config)

    async def startup_and_keepalive() -> None:
        scheduler.start()
        await run_pipeline_once(config)
        await _run_forever()

    try:
        asyncio.run(startup_and_keepalive())
    except KeyboardInterrupt:
        logger.info("收到退出信号，停止调度")
    finally:
        try:
            scheduler.shutdown(wait=False)
        except RuntimeError:
            pass  # 事件循环已关闭时忽略


if __name__ == "__main__":
    main()
