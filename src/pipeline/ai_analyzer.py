"""百炼 AI：小红书爆款文案生成器，联网搜索 + 资讯博主风格。"""
import json
import logging
from typing import Any, Dict, List, Optional

from litellm import acompletion
from tenacity import retry, stop_after_attempt, retry_if_exception_type

from .models import AnalysisItem

logger = logging.getLogger(__name__)

# ── 小红书资讯博主人设 ──────────────────────────────────────────
SYSTEM_PROMPT = """\
你是一个真实的人，不是AI。你是一位95后小红书博主，关注时事但不是媒体人。
你写东西就像发朋友圈、跟闺蜜/兄弟吐槽一样自然。

你的说话习惯：
- 会用短句，会用省略号，会突然蹦一句感叹
- 偶尔打错字也没关系（但别故意）
- 会说"救命""离谱""笑死""我真的会谢""属于是"这种话
- 不会说"据悉""据了解""据报道""引发关注""引发热议"这种新闻腔
- 不会用"首先其次最后"这种作文结构
- emoji用得克制，关键地方点缀就行，不要每段都加
- 不说空话套话，每句话都有信息量

【核心要求】
1. 先联网搜索这条新闻的最新进展、网友热评、争议焦点
2. 把搜到的网友评论用自己的话转述，不要直接复制粘贴
3. 写出来的东西要让人觉得"这人是真的了解这件事"，不是看了个标题就来写
4. 有自己的立场和态度，但说话留余地，不要太绝对

严格按以下 JSON 输出，不要 markdown 代码块，不要其他任何文字：
{
  "key_points": ["要点1", "要点2", "要点3"],
  "sentiment": "positive|neutral|negative",
  "topic": "主题标签",
  "summary": "一句话概括（≤50字）",
  "xhs_title": "小红书标题（严格不超过20个中文字！多了会发布失败！）",
  "xhs_content": "小红书正文（完整文案，500-1000字）",
  "xhs_tags": ["标签1", "标签2", "标签3", "标签4", "标签5"]
}\
"""

# ── 单条 prompt 模板 ─────────────────────────────────────────────
SINGLE_USER_TEMPLATE = """\
请为以下热点新闻写一篇小红书帖子：

标题：{title}
{url_line}\
{platform_line}

【写作步骤】
第一步：联网搜索这条新闻的详细报道、最新进展、各方评论
第二步：把你搜到的信息消化吸收，用自己的话重新组织
第三步：按下面的结构写文案

【正文结构】
1. 开头（1-2句）：用一句让人想继续看下去的话开场。可以是疑问、感叹、或者一个反常识的事实。别用"今天给大家分享"这种。
2. 事情经过（3-5句）：把事情讲清楚，要有细节，不是干巴巴的复述标题。写出"你是真的了解这件事"的感觉。
3. 网友怎么说（2-3句）：搜索热门评论，用自己的话转述2-3条有代表性的观点。比如"评论区有人说得挺好的，xxx"、"也有人觉得xxx"
4. 你的看法（2-3句）：亮明态度但别太绝对。像跟朋友聊天一样说出你的想法，可以纠结，可以吐槽，但要真诚。
5. 结尾（1句）：抛个问题或者留个悬念，让人想评论。

【标题要求】
- xhs_title 严格≤20个中文字，超过会发布失败！
- 要么有悬念（"…的真相"），要么有情绪（"绝了""离谱"），要么有数字
- 别用感叹号堆砌

【标签要求】
- xhs_tags 5个，前2个是事件核心词，后3个是流量词（热搜/吃瓜/涨知识之类的）

【禁止事项】
- 禁止用"让我们一起""不禁让人""值得我们深思"这种AI腔
- 禁止用"宝子们""家人们""姐妹们"开头
- 禁止每段都加emoji
- 禁止写成新闻稿或者官方通报的语气\
"""

# ── 批量 prompt 模板 ─────────────────────────────────────────────
BATCH_USER_TEMPLATE = """\
请为以下每条热点新闻分别写一篇小红书帖子。

{items_block}

【要求】同单条：联网搜索背景和网友评论，用自己的话写，标题≤20字，正文500-1000字（经过/网友说/你的看法/互动结尾），5个标签。
禁止AI腔、新闻腔、套话。

严格按 JSON 数组输出，数组长度={count}，顺序对应，不要 markdown 代码块。
每项格式同 system prompt 中的 JSON schema。\
"""


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
            xhs_title=obj.get("xhs_title", ""),
            xhs_content=obj.get("xhs_content", ""),
            xhs_tags=obj.get("xhs_tags") or [],
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
        for item in arr:
            if isinstance(item, dict):
                try:
                    out.append(AnalysisItem(
                        key_points=item.get("key_points") or [],
                        sentiment=item.get("sentiment", "neutral"),
                        topic=item.get("topic", ""),
                        summary=item.get("summary", ""),
                        xhs_title=item.get("xhs_title", ""),
                        xhs_content=item.get("xhs_content", ""),
                        xhs_tags=item.get("xhs_tags") or [],
                    ))
                except Exception:
                    out.append(None)
            else:
                out.append(None)
        return out
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("解析批量 JSON 失败: %s", e)
        return []


def _empty_item() -> AnalysisItem:
    return AnalysisItem(
        key_points=[], sentiment="neutral", topic="", summary="",
        xhs_title="", xhs_content="", xhs_tags=[],
    )


class AIAnalyzer:
    """百炼 AI 分析器：小红书爆款文案生成，支持联网搜索。"""

    def __init__(
        self,
        api_base: str,
        model: str,
        api_key: str,
        timeout: int = 120,
        max_retries: int = 3,
        batch_size: int = 10,
        enable_search: bool = True,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = _resolve_api_key(api_key)
        self.timeout = timeout
        self.max_retries = max_retries
        self.batch_size = batch_size if batch_size and batch_size > 0 else 0
        self.enable_search = enable_search
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
            "temperature": 0.7,
            "max_tokens": 4000,
        }
        if self.api_key:
            params["api_key"] = self.api_key
        if self.api_base:
            params["api_base"] = self.api_base
        # 百炼 DashScope 联网搜索
        if self.enable_search:
            params["extra_body"] = {"enable_search": True}
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
        """单条分析：生成小红书爆款文案。"""
        url_line = f"链接：{url}\n" if url else ""
        platform_line = f"平台：{platform}\n" if platform else ""
        user_content = SINGLE_USER_TEMPLATE.format(
            title=title, url_line=url_line, platform_line=platform_line,
        )
        try:
            text = await self._call([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            item = _parse_single(text)
            if item:
                return item
            logger.warning("解析失败，原始输出: %s", text[:500])
        except Exception as e:
            logger.exception("单条分析请求失败: %s", e)
        return _empty_item()

    async def analyze_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> List[AnalysisItem]:
        """批量分析。返回与 items 等长的 AnalysisItem 列表。"""
        if not items:
            return []

        mode = "单条" if self.batch_size <= 0 else "批量"
        logger.info("分析模式=%s batch_size=%s 总条数=%d", mode, self.batch_size if self.batch_size > 0 else "N/A", len(items))

        if self.batch_size <= 0:
            out: List[AnalysisItem] = []
            for i, one in enumerate(items):
                title = one.get("title") or ""
                url = one.get("url") or ""
                platform = one.get("platform_name") or one.get("platform") or ""
                out.append(await self.analyze_one(title, url, platform))
                if (i + 1) % 10 == 0:
                    logger.info("单条分析进度 %d/%d", i + 1, len(items))
            return out

        results: List[AnalysisItem] = []
        for start in range(0, len(items), self.batch_size):
            chunk = items[start : start + self.batch_size]
            part = await self._analyze_chunk(chunk)
            results.extend(part)
        return results

    async def _analyze_chunk(self, chunk: List[Dict[str, Any]]) -> List[AnalysisItem]:
        """对一批条目发一条请求。"""
        lines = []
        for i, one in enumerate(chunk):
            title = one.get("title") or ""
            url = one.get("url") or ""
            platform = one.get("platform_name") or one.get("platform") or ""
            lines.append(f"[{i+1}] 标题：{title}" + (f" 链接：{url}" if url else "") + (f" 平台：{platform}" if platform else ""))

        user_content = BATCH_USER_TEMPLATE.format(
            items_block="\n\n".join(lines),
            count=len(chunk),
        )
        try:
            text = await self._call([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ])
            parsed = _parse_batch(text)
            success_count = sum(1 for p in parsed if p is not None)
            logger.info("本批条数=%d 解析成功=%d", len(chunk), success_count)
            while len(parsed) < len(chunk):
                parsed.append(None)
            return [p if p else _empty_item() for p in parsed[: len(chunk)]]
        except Exception as e:
            logger.exception("批量分析请求失败: %s", e)
            return [_empty_item() for _ in chunk]
