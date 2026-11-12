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
from ..stages import CleanStage, FileSink, ProfileStage, ReduceStage, RenameColumnsStage, TransformStage
from ..stages.rename import resolve_column_aliases
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
            instruction = params.get("instruction")  # 用户原始需求，指导 AI 提取字段
            return WebSource(url=url, site=kind, keyword=keyword, instruction=instruction)
        try:
            return GenericSource(kind, **params)
        except Exception as exc:
            raise PlanError(f"source={kind} 通用源初始化失败: {exc}") from exc

    # fallback 到旧的硬编码类
    return _instantiate(SOURCE_CLASSES, spec, "source")


def build_stage(spec: Dict[str, Any], column_aliases: Optional[Dict[str, str]] = None) -> Stage:
    # 别名只影响可视化渲染，其他环节仍然按原始列名工作
    if column_aliases and spec.get("type") == "viz":
        params = dict(spec.get("params") or {})
        params["column_aliases"] = column_aliases
        spec = {**spec, "params": params}
    return _instantiate(STAGE_CLASSES, spec, "stage")


def _expected_columns(plan: Dict[str, Any], profile: Optional[Dict[str, Any]]) -> list:
    """组装期能确定列名时才做 AI 映射。

    web 源例外：AI 提取时已经按用户需求直接产出中文字段名，
    再做一次映射只会把列名改乱。
    """
    if profile and profile.get("fields"):
        return list(profile["fields"].keys())

    source = plan.get("source") or {}
    kind = source.get("type")
    if not kind or kind == "web":
        return []

    # 本地 CSV 没传画像时，读个表头就够（nrows=0，不加载数据）
    if kind == "csv":
        path = (source.get("params") or {}).get("path")
        if path:
            try:
                import pandas as pd

                return list(pd.read_csv(path, nrows=0).columns)
            except Exception:
                return []
        return []

    from .planner import BILIBILI_FIELDS, DOUBAN_FIELDS

    if kind in {"bilibili_hot", "bilibili_search", "bilibili_hot_async"}:
        return list(BILIBILI_FIELDS)
    if kind == "douban_top":
        return list(DOUBAN_FIELDS)

    cfg = get_source_config(kind)
    if cfg and cfg.get("fetcher") != "web":
        return list((cfg.get("fields") or {}).keys())
    return []


def build_pipeline(
    plan: Dict[str, Any],
    name: str = "ai_pipeline",
    log_dir: Optional[str] = "logs",
    profile: Optional[Dict[str, Any]] = None,
) -> Pipeline:
    """按计划组装管道。默认会先校验一遍，避免拿半成品计划去跑。

    列名契约：计划与管道内部一律使用数据源原始列名；用户别名在组装期
    解析一次，同时交给 VizStage（渲染用）和末尾的 RenameColumnsStage
    （导出/预览用），保证两边不会错位。
    """
    validate_plan(plan, profile)

    pipeline = Pipeline(name, log_dir=log_dir)

    aliases: Dict[str, str] = {}
    instruction = plan.get("instruction")
    if instruction:
        columns = _expected_columns(plan, profile)
        if columns:
            try:
                aliases = resolve_column_aliases(instruction, columns)
            except Exception as exc:
                # 别名是展示增强，模型抖动不该让整条管道失败，但要留痕
                aliases = {}
                pipeline.notes.append(f"⚠️ 列名映射解析失败，使用原始列名: {exc}")

    pipeline.add_stage(build_source(plan["source"]))

    for spec in plan["stages"]:
        # 跳过无效的 reduce（group_by 为 None 或 agg 为空）
        if spec.get("type") == "reduce":
            params = spec.get("params") or {}
            if params.get("group_by") is None or not params.get("agg"):
                continue
        pipeline.add_stage(build_stage(spec, column_aliases=aliases or None))

    # 放在所有环节之后：存储/聚合/校验全部走稳定列名，最终产物才是用户字段名
    if aliases:
        pipeline.add_stage(RenameColumnsStage(mapping=aliases))
    return pipeline
