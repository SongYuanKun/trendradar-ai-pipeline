"""Publish AI analysis results as Koen tools-site blog posts."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .models import AnalysisItem

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BlogPublishResult:
    slug: str
    markdown_path: Path
    html_path: Path
    committed: bool
    pushed: bool


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _slugify(text: str, date_prefix: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "trendradar-ai-note"
    return f"{date_prefix}-{s[:48].strip('-')}"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tags(analysis: AnalysisItem) -> list[str]:
    tags = [t.strip().lstrip("#") for t in analysis.xhs_tags if t and t.strip()]
    for fallback in (analysis.topic, "AI", "趋势观察"):
        if fallback and fallback not in tags:
            tags.append(fallback)
    return tags[:5]


def _source_title(source: Dict[str, Any]) -> str:
    return str(source.get("title") or "").strip()


def build_blog_markdown(
    analysis: AnalysisItem,
    source: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> tuple[str, str]:
    """Return (slug, markdown) for one blog post."""
    source = source or {}
    now = now or datetime.now()
    date = now.strftime("%Y-%m-%d")

    original_title = _source_title(source)
    title = analysis.xhs_title or analysis.topic or original_title or "TrendRadar 技术趋势观察"
    if len(title) < 8 and original_title:
        title = original_title
    slug = _slugify(title, date)

    tags = _tags(analysis)
    description = analysis.summary or f"TrendRadar 自动筛选并分析：{original_title or title}"
    source_url = str(source.get("url") or "").strip()
    platform = str(source.get("platform_name") or source.get("platform") or "").strip()
    key_points = [p for p in analysis.key_points if p]

    frontmatter = [
        "---",
        f"title: {_yaml_string(title)}",
        f"date: {date}",
        f"description: {_yaml_string(description[:150])}",
        f"keywords: {_yaml_string(','.join(tags))}",
        f"tags: [{', '.join(_yaml_string(t) for t in tags)}]",
        "kicker: \"TrendRadar 自动观察 · AI 实践\"",
        "section: \"AI 实践\"",
        f"slug: {slug}",
        "---",
        "",
    ]

    intro = analysis.summary or "这是一条由 TrendRadar 自动筛选出的技术热点。"
    content = analysis.xhs_content or intro

    lines = frontmatter + [
        f"> 这篇来自 TrendRadar 的自动分析草稿，保留了来源和判断链路，方便后续人工扩写或复盘。",
        "",
        "## 这条趋势在说什么",
        "",
        intro,
        "",
    ]

    if original_title or source_url or platform:
        lines += ["## 原始信号", ""]
        if original_title:
            lines.append(f"- 标题：{original_title}")
        if platform:
            lines.append(f"- 来源：{platform}")
        if source_url:
            lines.append(f"- 链接：[{source_url}]({source_url})")
        lines.append("")

    if key_points:
        lines += ["## 关键判断", ""]
        lines.extend(f"- {point}" for point in key_points[:6])
        lines.append("")

    lines += [
        "## Koen 的技术视角",
        "",
        content,
        "",
        "## 可以继续追的问题",
        "",
        "- 这件事会不会改变开发者真实工作流，而不只是制造一轮短期关注？",
        "- 有没有官方文档、GitHub issue、产品更新日志能进一步验证？",
        "- 如果要落到自己的工具或内容产品里，最小可试验版本是什么？",
        "",
        "这类内容后续会继续沉淀到 Koen 工具箱，作为技术趋势观察和独立开发选题库的一部分。",
        "",
    ]
    return slug, "\n".join(lines)


def _upsert_blog_index(tools_repo: Path, slug: str, title: str, date: str, description: str, tags: list[str]) -> None:
    path = tools_repo / "data" / "blog-posts.js"
    if not path.is_file():
        logger.warning("blog index not found: %s", path)
        return

    text = path.read_text(encoding="utf-8")
    if f'slug: "{slug}"' in text or f"slug: '{slug}'" in text:
        return

    item = (
        "  {\n"
        f"    slug: {json.dumps(slug, ensure_ascii=False)},\n"
        f"    title: {json.dumps(title, ensure_ascii=False)},\n"
        f"    date: {json.dumps(date, ensure_ascii=False)},\n"
        "    author: \"Koen\",\n"
        "    category: \"AI 实践\",\n"
        f"    tags: {json.dumps(tags, ensure_ascii=False)},\n"
        f"    description: {json.dumps(description, ensure_ascii=False)},\n"
        "    readTime: \"5 分钟\",\n"
        f"    url: \"post.html?slug={slug}\"\n"
        "  },\n"
    )
    marker = "var BLOG_POSTS_DATA = [\n"
    if marker not in text:
        logger.warning("blog index marker not found: %s", path)
        return
    path.write_text(text.replace(marker, marker + item, 1), encoding="utf-8")


def publish_blog_post(
    config: Dict[str, Any],
    analysis: AnalysisItem,
    source: Optional[Dict[str, Any]] = None,
) -> Optional[BlogPublishResult]:
    """Generate a tools-site blog post and optionally commit/push it."""
    blog_cfg = config.get("blog") or {}
    if not blog_cfg.get("enabled", False):
        logger.info("blog.enabled=false，跳过博客输出")
        return None

    tools_repo = Path(blog_cfg.get("repo_path") or "/home/kun/vs_code/dev-tools-nav").expanduser()
    if not tools_repo.is_dir():
        logger.error("tools repo not found: %s", tools_repo)
        return None

    source = source or {}
    now = datetime.now()
    date = now.strftime("%Y-%m-%d")
    slug, markdown = build_blog_markdown(analysis, source, now=now)

    content_dir = tools_repo / "content" / "blog"
    content_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = content_dir / f"{slug}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    title = analysis.xhs_title or analysis.topic or _source_title(source) or "TrendRadar 技术趋势观察"
    description = analysis.summary or f"TrendRadar 自动筛选并分析：{_source_title(source) or title}"
    tags = _tags(analysis)
    _upsert_blog_index(tools_repo, slug, title, date, description[:150], tags)

    build = _run(["node", "scripts/build-blog.mjs"], cwd=tools_repo, timeout=120)
    if build.returncode != 0:
        logger.error("blog build failed: %s", build.stderr[-1000:])
        return None

    html_path = tools_repo / "pages" / "blog" / f"{slug}.html"
    if not html_path.is_file():
        logger.error("blog html not generated: %s", html_path)
        return None

    commit_enabled = bool(blog_cfg.get("commit", True))
    push_enabled = bool(blog_cfg.get("push", False))
    committed = False
    pushed = False

    if commit_enabled:
        paths = [
            str(markdown_path.relative_to(tools_repo)),
            str(html_path.relative_to(tools_repo)),
            "data/blog-posts.js",
        ]
        _run(["git", "add", *paths], cwd=tools_repo)
        diff = _run(["git", "diff", "--cached", "--quiet"], cwd=tools_repo)
        if diff.returncode != 0:
            msg = f"Add TrendRadar blog post: {title[:48]}"
            commit = _run(["git", "commit", "-m", msg], cwd=tools_repo, timeout=120)
            if commit.returncode != 0:
                logger.error("blog commit failed: %s", commit.stderr[-1000:])
                return None
            committed = True
        else:
            logger.info("博客输出无新变更，跳过提交")

    if push_enabled and committed:
        push = _run(["git", "push"], cwd=tools_repo, timeout=120)
        if push.returncode != 0:
            logger.error("blog push failed: %s", push.stderr[-1000:])
            return None
        pushed = True

    logger.info("博客输出完成: %s", html_path)
    return BlogPublishResult(slug, markdown_path, html_path, committed, pushed)
