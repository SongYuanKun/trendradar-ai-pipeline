"""Pydantic 模型：分析结果 schema、下游请求体。"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ---------- 单条分析结果（百炼输出） ----------


class AnalysisItem(BaseModel):
    """单条内容的 AI 分析结果 + 小红书文案。"""

    key_points: List[str] = Field(default_factory=list, description="关键信息提取")
    sentiment: str = Field(default="neutral", description="情感: positive/neutral/negative")
    topic: str = Field(default="", description="主题分类")
    summary: str = Field(default="", description="内容摘要")
    # 小红书爆款文案字段
    xhs_title: str = Field(default="", description="小红书标题(≤20字)")
    xhs_content: str = Field(default="", description="小红书正文(完整文案)")
    xhs_tags: List[str] = Field(default_factory=list, description="小红书标签")


# ---------- 带来源的单条（投递下游） ----------


class SourceMeta(BaseModel):
    """原文元数据。"""

    title: str = ""
    url: str = ""
    platform: str = ""
    rank: int = 0


class IngestItem(BaseModel):
    """投递到 xiaohongshu-mcp 的单条：分析结果 + 可选来源。"""

    key_points: List[str] = Field(default_factory=list)
    sentiment: str = "neutral"
    topic: str = ""
    summary: str = ""
    source: Optional[SourceMeta] = None


class IngestPayload(BaseModel):
    """POST 到 xiaohongshu-mcp 的请求体。"""

    items: List[IngestItem] = Field(default_factory=list)
    batch_id: str = ""
    ts: str = ""
