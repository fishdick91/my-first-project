"""把 JSON 计划翻译成真正的 Stage 对象。

这里没有任何 eval / exec：
计划里的 type 只是一个字符串，我们用字典查表拿到对应的类，
参数原样传给构造函数。模型能做的只有"选环节、填参数"，
碰不到 Python 代码，这是整条 AI 链路的安全底线。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Type

from ..core.pipeline import Pipeline
from ..core.stage import Stage
from ..sources import (
    AsyncBilibiliHotSource,
    BilibiliHotSource,
    BilibiliSearchSource,
    CsvSource,
    DoubanTopSource,
)
from ..sources.generic import GenericSource
from ..sources.registry import get_source_config
from ..sources.web_source import WebSource
from ..stages import CleanStage, FileSink, ProfileStage, ReduceStage, TransformStage
from ..storage import DuckDBStore, MysqlStore
from ..viz import VizStage
from .planner import PlanError, validate_plan

# 旧的硬编码源（作为 fallback，逐步迁移到 YAML 配置）
SOURCE_CLASSES: Dict[str, Type[Stage]] = {
    "csv": CsvSource,
    "bilibili_hot": BilibiliHotSource,
    "bilibili_search": BilibiliSearchSource,
    "bilibili_hot_async": AsyncBilibiliHotSource,
    "douban_top": DoubanTopSource,
    "web": WebSource,  # 通用网页爬虫，输入任意 URL
}

STAGE_CLASSES: Dict[str, Type[Stage]] = {
    "clean": CleanStage,
    "transform": TransformStage,
    "reduce": ReduceStage,
    "profile": ProfileStage,
    "duckdb": DuckDBStore,
    "mysql": MysqlStore,
    "file_sink": FileSink,
    "viz": VizStage,
}


def _instantiate(
    classes: Dict[str, Type[Stage]], spec: Dict[str, Any], label: str
) -> Stage:
    kind = spec.get("type")
    cls = classes.get(kind)
    if cls is None:
        raise PlanError(f"{label} 类型不支持: {kind}（可选 {sorted(classes)}）")

    params = spec.get("params") or {}
    try:
        return cls(**params)
    except TypeError as exc:
        # 模型多塞了不认识的参数时，给一句人话，而不是干巴巴的 TypeError
        raise PlanError(f"{label}={kind} 参数有误: {exc}") from exc


def build_source(spec: Dict[str, Any]) -> Stage:
    kind = spec.get("type")
    params = spec.get("params") or {}

    # 优先用通用数据源（YAML 配置）
    # douban_top 暂时保留旧类（with_review 详情页逻辑待迁移到 GenericSource）
    if kind != "douban_top" and get_source_config(kind) is not None:
        cfg = get_source_config(kind)
        if cfg.get("fetcher") == "web":
            # HTML 网页，用 WebSource（AI 自动分析页面，无需预配置字段）
            url = params.get("url") or cfg.get("url", "")
            keyword = params.get("keyword")
            return WebSource(url=url, site=kind, keyword=keyword)
        try:
            return GenericSource(kind, **params)
        except Exception as exc:
            raise PlanError(f"source={kind} 通用源初始化失败: {exc}") from exc

    # fallback 到旧的硬编码类
    return _instantiate(SOURCE_CLASSES, spec, "source")


def build_stage(spec: Dict[str, Any]) -> Stage:
    return _instantiate(STAGE_CLASSES, spec, "stage")


def build_pipeline(
    plan: Dict[str, Any],
    name: str = "ai_pipeline",
    log_dir: Optional[str] = "logs",
    profile: Optional[Dict[str, Any]] = None,
) -> Pipeline:
    """按计划组装管道。默认会先校验一遍，避免拿半成品计划去跑。"""
    validate_plan(plan, profile)

    pipeline = Pipeline(name, log_dir=log_dir)
    pipeline.add_stage(build_source(plan["source"]))
    for spec in plan["stages"]:
        # 跳过无效的 reduce（group_by 为 None 或 agg 为空）
        if spec.get("type") == "reduce":
            params = spec.get("params") or {}
            if params.get("group_by") is None or not params.get("agg"):
                continue
        pipeline.add_stage(build_stage(spec))
    return pipeline
