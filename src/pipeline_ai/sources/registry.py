"""数据源注册表：从 config/sources.yaml 加载所有数据源配置。

新增数据源只需在 YAML 里加一条，不用写 Python 代码。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

# 配置文件路径（项目根目录下的 config/sources.yaml）
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "config",
    "sources.yaml",
)

_registry: Optional[Dict[str, Any]] = None


def _load() -> Dict[str, Any]:
    global _registry
    if _registry is not None:
        return _registry
    if not os.path.exists(_CONFIG_PATH):
        _registry = {}
        return _registry
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    _registry = data.get("sources", {})
    return _registry


def reload() -> None:
    """重新加载配置（修改 YAML 后调用）。"""
    global _registry
    _registry = None
    _load()


def get_source_config(source_type: str) -> Optional[Dict[str, Any]]:
    """获取指定数据源的配置。"""
    return _load().get(source_type)


def list_sources() -> List[str]:
    """列出所有已注册的数据源类型。"""
    return list(_load().keys())


def get_label(source_type: str) -> str:
    """获取数据源的中文标签。"""
    cfg = get_source_config(source_type)
    return cfg.get("label", source_type) if cfg else source_type


def get_fields(source_type: str) -> Dict[str, str]:
    """获取数据源的字段映射（输出列名 -> JSON/CSS 路径）。

    如果配置里 fields 为空，说明抓全部字段，返回空 dict。
    """
    cfg = get_source_config(source_type)
    return cfg.get("fields", {}) if cfg else {}


def is_catch_all(source_type: str) -> bool:
    """该数据源是否抓取全部字段（fields 为空）。"""
    return len(get_fields(source_type)) == 0


def get_search_url(source_type: str) -> Optional[str]:
    """获取数据源的搜索 URL 模板（含 {keyword} 占位符），没有则返回 None。"""
    cfg = get_source_config(source_type)
    return cfg.get("search_url") if cfg else None


def get_all_fields() -> Dict[str, List[str]]:
    """获取所有数据源的字段列表（用于生成 planner 提示词）。"""
    result = {}
    for src_type in list_sources():
        result[src_type] = list(get_fields(src_type).keys())
    return result
