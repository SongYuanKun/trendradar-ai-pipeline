"""投递到 xiaohongshu-mcp。"""
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, retry_if_exception_type

from .models import AnalysisItem, IngestItem, IngestPayload, SourceMeta

logger = logging.getLogger(__name__)

RETRY_QUEUE_DIR = "retry_queue"


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


def _ensure_retry_dir() -> Path:
    d = Path(RETRY_QUEUE_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_ingest_item(analysis: AnalysisItem, source: Optional[Dict[str, Any]] = None) -> IngestItem:
    meta = None
    if source:
        meta = SourceMeta(
            title=source.get("title", ""),
            url=source.get("url", ""),
            platform=source.get("platform_name") or source.get("platform", ""),
            rank=source.get("rank", 0),
        )
    return IngestItem(
        key_points=analysis.key_points,
        sentiment=analysis.sentiment,
        topic=analysis.topic,
        summary=analysis.summary,
        source=meta,
    )


def build_payload(
    analyses: List[AnalysisItem],
    sources: Optional[List[Dict[str, Any]]] = None,
    batch_id: Optional[str] = None,
) -> IngestPayload:
    """组装投递请求体。"""
    if sources is None:
        sources = [{}] * len(analyses)
    while len(sources) < len(analyses):
        sources.append({})
    items = [
        _build_ingest_item(a, s)
        for a, s in zip(analyses, sources)
    ]
    bid = batch_id or str(uuid.uuid4())
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return IngestPayload(items=items, batch_id=bid, ts=ts)


@retry(
    stop=stop_after_attempt(2),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
    reraise=True,
)
async def send_to_downstream(
    url: str,
    payload: IngestPayload,
    timeout: float = 30.0,
    api_key: Optional[str] = None,
) -> bool:
    """
    将 payload POST 到 xiaohongshu-mcp。
    失败时写入 retry_queue 目录，返回 False。
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = payload.model_dump(mode="json")

    logger.info("投递下游 url=%s items=%d batch_id=%s", _url_for_log(url), len(payload.items), payload.batch_id)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        logger.error("下游请求失败: %s", e)
        _save_retry(payload, str(e))
        return False

    if resp.status_code >= 400:
        logger.error("下游返回 %s: %s", resp.status_code, resp.text[:500])
        _save_retry(payload, f"HTTP {resp.status_code}: {resp.text[:200]}")
        return False

    logger.info("下游投递成功 batch_id=%s items=%d", payload.batch_id, len(payload.items))
    return True


def _save_retry(payload: IngestPayload, reason: str) -> None:
    """将失败批次写入 retry_queue 目录。"""
    try:
        d = _ensure_retry_dir()
        name = f"{payload.batch_id or payload.ts}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
        path = d / name
        data = {
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "payload": payload.model_dump(mode="json"),
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入重试队列: %s", path)
    except Exception as e:
        logger.exception("写入重试队列失败: %s", e)


def load_retry_queue() -> List[IngestPayload]:
    """从 retry_queue 目录加载待重试的 payload（供调度层重试）。"""
    d = Path(RETRY_QUEUE_DIR)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            payload = IngestPayload(**(data.get("payload") or data))
            out.append(payload)
        except Exception as e:
            logger.warning("读取重试文件 %s 失败: %s", f, e)
    return out
