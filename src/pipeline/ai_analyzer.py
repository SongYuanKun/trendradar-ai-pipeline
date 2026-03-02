"""百炼 AI：单条/批量、结构化 JSON。"""
import json
import logging
from typing import Any, Dict, List, Optional

from litellm import acompletion
from tenacity import retry, stop_after_attempt, retry_if_exception_type

from .models import AnalysisItem

logger = logging.getLogger(__name__)

# 单条输出 JSON schema 说明（与 AnalysisItem 一致）
SINGLE_SCHEMA = (
    '严格按以下 JSON 输出，不要 markdown 代码块，不要其他文字：\n'
    '{"key_points": ["要点1","要点2"], "sentiment": "positive|neutral|negative", '
    '"topic": "主题标签", "summary": "一段话摘要"}'
)
# 批量输出：JSON 数组，每项同上
BATCH_SCHEMA = (
    '严格按以下 JSON 数组输出，不要 markdown 代码块，不要其他文字。'
    '数组长度与输入的条数一致，顺序对应。每项格式：'
    '{"key_points": ["要点1"], "sentiment": "positive|neutral|negative", '
    '"topic": "主题", "summary": "摘要"}'
)


def _resolve_api_key(value: Any) -> str:
    if isinstance(value, str) and value.strip().startswith("env:"):
        import os
        key = value.strip()[4:].strip()
        return os.environ.get(key, "")
    return str(value) if value else ""


def _strip_json_block(raw: str) -> str:
    """去掉可能的 ```json ... ``` 包裹。"""
    if not raw or not raw.strip():
        return raw
    s = raw.strip()
    for prefix in ("```json", "```"):
        if s.startswith(prefix):
            s = s[len(prefix):].lstrip()
        if s.endswith("```"):
            s = s[:-3].rstrip()
    return s


def _parse_single(text: str) -> Optional[AnalysisItem]:
    """解析单条 JSON 为 AnalysisItem。"""
    try:
        s = _strip_json_block(text)
        obj = json.loads(s)
        if not isinstance(obj, dict):
            return None
        return AnalysisItem(
            key_points=obj.get("key_points") or [],
            sentiment=obj.get("sentiment", "neutral"),
            topic=obj.get("topic", ""),
            summary=obj.get("summary", ""),
        )
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("解析单条 JSON 失败: %s", e)
        return None


def _parse_batch(text: str) -> List[Optional[AnalysisItem]]:
    """解析批量 JSON 数组，返回与输入等长的列表，失败项为 None。"""
    try:
        s = _strip_json_block(text)
        arr = json.loads(s)
        if not isinstance(arr, list):
            return []
        out: List[Optional[AnalysisItem]] = []
        for i, item in enumerate(arr):
            if isinstance(item, dict):
                try:
                    out.append(AnalysisItem(
                        key_points=item.get("key_points") or [],
                        sentiment=item.get("sentiment", "neutral"),
                        topic=item.get("topic", ""),
                        summary=item.get("summary", ""),
                    ))
                except Exception:
                    out.append(None)
            else:
                out.append(None)
        return out
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("解析批量 JSON 失败: %s", e)
        return []


class AIAnalyzer:
    """百炼 AI 分析器：单条/批量、结构化 JSON。"""

    def __init__(
        self,
        api_base: str,
        model: str,
        api_key: str,
        timeout: int = 120,
        max_retries: int = 3,
        batch_size: int = 10,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = _resolve_api_key(api_key)
        self.timeout = timeout
        self.max_retries = max_retries
        # batch_size <= 0 表示单条模式；否则为每批条数
        self.batch_size = batch_size if batch_size and batch_size > 0 else 0
        # LiteLLM 需要 provider 前缀：百炼/ DashScope 用 dashscope/模型名；其他兼容接口用 openai/模型名
        if "dashscope" in (self.api_base or "").lower():
            self._litellm_model = f"dashscope/{self.model}"
        elif self.api_base:
            self._litellm_model = f"openai/{self.model}"
        else:
            self._litellm_model = self.model

    def _build_params(self) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self._litellm_model,
            "timeout": self.timeout,
            "num_retries": self.max_retries,
            "temperature": 0.3,
            "max_tokens": 2000,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base
        return params

    @retry(
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
        reraise=True,
    )
    async def _call(self, messages: List[Dict[str, str]]) -> str:
        params = self._build_params()
        params["messages"] = messages
        response = await acompletion(**params)
        return response.choices[0].message.content or ""

    async def analyze_one(self, title: str, url: str = "", platform: str = "") -> AnalysisItem:
        """单条分析，返回 AnalysisItem；失败时返回带空字段的占位。"""
        user_content = (
            f"请对以下新闻标题做：1) 关键信息提取 2) 情感分析 3) 主题分类 4) 内容摘要。\n"
            f"标题：{title}\n"
            f"{'链接：' + url if url else ''}\n"
            f"{'平台：' + platform if platform else ''}\n\n"
            f"{SINGLE_SCHEMA}"
        )
        try:
            text = await self._call([
                {"role": "system", "content": "你只输出合法 JSON，不要 markdown 代码块，不要其他说明。"},
                {"role": "user", "content": user_content},
            ])
            item = _parse_single(text)
            if item:
                return item
        except Exception as e:
            logger.exception("单条分析请求失败: %s", e)
        return AnalysisItem(
            key_points=[],
            sentiment="neutral",
            topic="",
            summary="",
        )

    async def analyze_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> List[AnalysisItem]:
        """
        批量分析。items 每项至少含 title，可选 url、platform。
        返回与 items 等长的 AnalysisItem 列表，失败项为占位 AnalysisItem。
        """
        if not items:
            return []

        mode = "单条" if self.batch_size <= 0 else "批量"
        logger.info("分析模式=%s batch_size=%s 总条数=%d", mode, self.batch_size if self.batch_size > 0 else "N/A", len(items))

        if self.batch_size <= 0:
            # 单条模式：逐条请求
            out: List[AnalysisItem] = []
            for i, one in enumerate(items):
                title = one.get("title") or ""
                url = one.get("url") or ""
                platform = one.get("platform_name") or one.get("platform") or ""
                out.append(await self.analyze_one(title, url, platform))
                if (i + 1) % 10 == 0:
                    logger.info("单条分析进度 %d/%d", i + 1, len(items))
            return out

        # 批量模式：按 batch_size 分批
        results: List[AnalysisItem] = []
        for start in range(0, len(items), self.batch_size):
            chunk = items[start : start + self.batch_size]
            part = await self._analyze_chunk(chunk)
            results.extend(part)
        return results

    async def _analyze_chunk(self, chunk: List[Dict[str, Any]]) -> List[AnalysisItem]:
        """对一批条目发一条请求，返回解析后的列表。"""
        lines = []
        for i, one in enumerate(chunk):
            title = one.get("title") or ""
            url = one.get("url") or ""
            platform = one.get("platform_name") or one.get("platform") or ""
            lines.append(f"[{i+1}] 标题：{title}" + (f" 链接：{url}" if url else "") + (f" 平台：{platform}" if platform else ""))

        user_content = (
            "请对以下每条新闻做：1) 关键信息提取 2) 情感分析 3) 主题分类 4) 内容摘要。\n\n"
            + "\n\n".join(lines)
            + "\n\n"
            + BATCH_SCHEMA
        )
        try:
            text = await self._call([
                {"role": "system", "content": "你只输出合法 JSON 数组，不要 markdown 代码块，不要其他说明。"},
                {"role": "user", "content": user_content},
            ])
            parsed = _parse_batch(text)
            success_count = sum(1 for p in parsed if p is not None)
            logger.info("本批条数=%d 解析成功=%d", len(chunk), success_count)
            # 补齐长度
            while len(parsed) < len(chunk):
                parsed.append(None)
            return [p if p else AnalysisItem(key_points=[], sentiment="neutral", topic="", summary="") for p in parsed[: len(chunk)]]
        except Exception as e:
            logger.exception("批量分析请求失败: %s", e)
            return [AnalysisItem(key_points=[], sentiment="neutral", topic="", summary="") for _ in chunk]
