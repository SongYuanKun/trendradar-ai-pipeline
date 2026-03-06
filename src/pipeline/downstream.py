"""通过 MCP 协议调用 xiaohongshu-mcp 的 publish_content 发布内容。"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from tenacity import retry, stop_after_attempt, retry_if_exception_type

from .models import AnalysisItem, SourceMeta

logger = logging.getLogger(__name__)

RETRY_QUEUE_DIR = "retry_queue"

# 默认封面图（公开可访问的纯色渐变图）
DEFAULT_COVER_IMAGE = "https://picsum.photos/1080/1440"


def _url_for_log(url: str) -> str:
    if not url or not url.strip():
        return "(未配置)"
    try:
        p = urlparse(url.strip())
        netloc = p.netloc or "(empty)"
        return f"{p.scheme or 'http'}://{netloc}"
    except Exception:
        return "(解析失败)"


def _ensure_dir(path: str) -> Path:
    d = Path(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_publish_params(
    analysis: AnalysisItem,
    source: Optional[Dict[str, Any]] = None,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """将分析结果转换为 publish_content 所需的参数。优先使用 AI 生成的小红书文案。"""
    source = source or {}
    original_title = source.get("title", "")

    # title: 优先用 AI 生成的小红书标题，硬截断 ≤20 字
    raw_title = analysis.xhs_title or original_title or analysis.topic or "热点速递"
    title = raw_title[:20]

    # content: 优先用 AI 生成的完整文案
    if analysis.xhs_content:
        content = analysis.xhs_content
    else:
        # fallback: 旧逻辑拼接
        platform = source.get("platform_name") or source.get("platform", "")
        parts = []
        if analysis.summary:
            parts.append(analysis.summary)
        if analysis.key_points:
            parts.append("")
            for kp in analysis.key_points:
                parts.append(f"• {kp}")
        if platform:
            parts.append(f"\n📡 来源：{platform}")
        content = "\n".join(parts)

    # tags: 优先用 AI 生成的标签
    tags = analysis.xhs_tags if analysis.xhs_tags else [analysis.topic or "热点", "新闻", "热搜"]

    # image
    image_url = image_path or DEFAULT_COVER_IMAGE

    return {
        "title": title,
        "content": content,
        "images": [image_url],
        "tags": tags,
    }


def _build_proxy_url(proxy_base: str, mcp_url: str) -> str:
    """构造 Inspector 风格的代理 URL。"""
    encoded = quote(mcp_url, safe="")
    return f"{proxy_base.rstrip('/')}/mcp?url={encoded}&transportType=streamable-http"


@retry(
    stop=stop_after_attempt(2),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
async def send_to_downstream(
    mcp_url: str,
    analysis: AnalysisItem,
    source: Optional[Dict[str, Any]] = None,
    image_path: Optional[str] = None,
    proxy_base: Optional[str] = None,
    proxy_auth: Optional[str] = None,
    api_token: Optional[str] = None,
) -> bool:
    """
    通过 MCP 协议调用 xiaohongshu-mcp 的 publish_content 发布内容。
    proxy_base: 本地代理地址（如 http://localhost:6277），通过 StreamableHttpTransport 转发。
    proxy_auth: x-mcp-proxy-auth header 值。
    失败时写入 retry_queue，返回 False。
    """
    params = build_publish_params(analysis, source, image_path)
    logger.info(
        "发布到小红书 mcp=%s title='%s' tags=%s",
        _url_for_log(proxy_base or mcp_url), params["title"], params["tags"],
    )

    try:
        if proxy_base and proxy_base.strip():
            proxy_url = _build_proxy_url(proxy_base.strip(), mcp_url)
            headers = {}
            if proxy_auth:
                headers["x-mcp-proxy-auth"] = proxy_auth
            if api_token:
                headers["x-api-token"] = api_token
                headers["x-custom-auth-headers"] = '["X-Api-Token"]'
            transport = StreamableHttpTransport(url=proxy_url, headers=headers)
            logger.info("通过代理连接: %s", _url_for_log(proxy_base))
            client_arg = transport
        else:
            client_arg = mcp_url

        async with Client(client_arg) as client:
            result = await asyncio.wait_for(
                client.call_tool("publish_content", params),
                timeout=300.0,
            )
    except Exception as e:
        logger.error("MCP publish_content 调用失败: %s", e)
        _save_retry(params, str(e))
        return False

    # 检查结果
    result_text = ""
    if hasattr(result, "content") and result.content:
        result_text = getattr(result.content[0], "text", str(result.content))

    if getattr(result, "is_error", False):
        logger.error("publish_content 返回错误: %s", result_text[:500])
        _save_retry(params, f"MCP error: {result_text[:200]}")
        return False

    logger.info("小红书发布成功: %s", result_text[:200])
    return True


def _save_retry(params: Dict[str, Any], reason: str) -> None:
    try:
        d = _ensure_dir(RETRY_QUEUE_DIR)
        name = f"retry_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}.json"
        data = {
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "params": params,
        }
        (d / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("已写入重试队列: %s", name)
    except Exception as e:
        logger.exception("写入重试队列失败: %s", e)


def load_retry_queue() -> List[Dict[str, Any]]:
    """加载待重试的发布参数。"""
    d = Path(RETRY_QUEUE_DIR)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append(data.get("params") or data)
        except Exception as e:
            logger.warning("读取重试文件 %s 失败: %s", f, e)
    return out
