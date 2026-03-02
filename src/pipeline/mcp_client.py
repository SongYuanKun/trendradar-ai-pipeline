"""调用 TrendRadar MCP 获取数据。"""
import json
import logging
from typing import Any, Dict, List, Optional

from fastmcp import Client
from tenacity import retry, stop_after_attempt, retry_if_exception_type

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
async def fetch_latest_news(
    base_url: str,
    limit: int = 50,
    include_url: bool = True,
    platforms: Optional[List[str]] = None,
    timeout: float = 60.0,
) -> List[Dict[str, Any]]:
    """
    从 TrendRadar MCP 获取最新新闻。

    Args:
        base_url: MCP 的 base URL，如 http://host:3333/mcp
        limit: 返回条数
        include_url: 是否包含 URL
        platforms: 平台 ID 列表，可选
        timeout: 单次请求超时（秒）

    Returns:
        新闻列表，每项含 title, platform, rank, url 等。若 MCP 返回 success=false 则抛异常。
    """
    import asyncio

    arguments: Dict[str, Any] = {
        "limit": limit,
        "include_url": include_url,
    }
    if platforms is not None:
        arguments["platforms"] = platforms

    logger.info("请求 MCP get_latest_news base_url=%s limit=%d include_url=%s", base_url, limit, include_url)
    async with Client(base_url) as client:
        result = await asyncio.wait_for(
            client.call_tool("get_latest_news", arguments, raise_on_error=False),
            timeout=timeout,
        )

    if getattr(result, "is_error", True):
        msg = "MCP get_latest_news 返回错误"
        if hasattr(result, "content") and result.content:
            part = getattr(result.content[0], "text", str(result.content))
            msg += f": {part[:500]}"
        logger.error(msg)
        raise RuntimeError(msg)

    # 解析 JSON：TrendRadar 返回 {"success": true, "data": [...]}
    raw = None
    if hasattr(result, "content") and result.content:
        raw = getattr(result.content[0], "text", None)
    if hasattr(result, "data") and result.data is not None:
        if isinstance(result.data, dict) and "data" in result.data:
            return result.data["data"]
        if isinstance(result.data, list):
            return result.data

    if not raw:
        raise RuntimeError("MCP get_latest_news 无返回内容")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("MCP 返回非 JSON: %s", e)
        raise

    if not isinstance(data, dict):
        raise RuntimeError("MCP 返回格式异常：非对象")

    if not data.get("success", False):
        err = data.get("error", {})
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        logger.error("MCP success=false: %s", msg)
        raise RuntimeError(f"MCP 返回失败: {msg}")

    news_list = data.get("data")
    if not isinstance(news_list, list):
        raise RuntimeError("MCP 返回格式异常：data 非数组")

    logger.info("MCP get_latest_news 获取 %d 条", len(news_list))
    return news_list
