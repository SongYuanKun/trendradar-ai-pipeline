"""加载 config.yaml 并解析 env: 占位。"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


def _resolve_value(v: Any) -> Any:
    if isinstance(v, str) and v.strip().startswith("env:"):
        key = v.strip()[4:].strip()
        return os.environ.get(key, "")
    if isinstance(v, dict):
        return {k: _resolve_value(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [_resolve_value(x) for x in v]
    return v


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """
    加载 config.yaml。若未指定路径，则依次查找：
    当前工作目录 config/config.yaml、项目根 config/config.yaml。
    """
    if config_path and Path(config_path).is_file():
        path = Path(config_path)
    else:
        cwd = Path.cwd()
        for base in (cwd, cwd.parent):
            p = base / "config" / "config.yaml"
            if p.is_file():
                path = p
                break
        else:
            path = cwd / "config" / "config.yaml"
    if not path.is_file():
        logger.warning("未找到配置文件，使用空配置: %s", path)
        data = {}
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        logger.info("已加载配置文件: %s", path.resolve())
    data = _resolve_value(data)
    # 环境变量覆盖（便于 Docker 部署）
    if os.environ.get("MCP_BASE_URL"):
        data.setdefault("mcp", {})["base_url"] = os.environ["MCP_BASE_URL"]
    if os.environ.get("XIAOHONGSHU_MCP_URL"):
        data.setdefault("downstream", {})["url"] = os.environ["XIAOHONGSHU_MCP_URL"]
    if os.environ.get("HTTP_PROXY"):
        data.setdefault("downstream", {})["proxy"] = os.environ["HTTP_PROXY"]
    elif os.environ.get("HTTPS_PROXY"):
        data.setdefault("downstream", {})["proxy"] = os.environ["HTTPS_PROXY"]
    if os.environ.get("BAILIAN_API_KEY"):
        data.setdefault("ai", {})["api_key"] = os.environ["BAILIAN_API_KEY"]
    return data
