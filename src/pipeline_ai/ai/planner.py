"""把一句自然语言翻译成管道计划（JSON）。

职责边界很重要：
    模型只负责"出主意"，输出的是一份 JSON 计划；
    真正干活（清洗、聚合、出图）的是 stages/ 里的代码。
这样模型再离谱，也最多写错配置，不可能执行任意代码。

计划长这样：

    {
      "title": "B站热门视频播放量排行",
      "reason": "用户想看播放量排名，所以按 views 取 Top 10",
      "source": {"type": "bilibili_hot", "params": {"limit": 30}},
      "stages": [
        {"type": "clean", "params": {"drop_duplicates": ["bvid"]}},
        {"type": "viz", "params": {"charts": [
            {"type": "bar_top", "x": "title", "y": "views", "top": 10}
        ]}}
      ]
    }
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional

from .llm import LLMClient

SOURCE_TYPES = {"bilibili_hot", "bilibili_hot_async", "csv"}
STAGE_TYPES = {"clean", "transform", "reduce", "profile", "duckdb", "mysql", "file_sink", "viz"}
CHART_TYPES = {"bar_top", "line", "scatter"}

# 没有数据画像时，模型只能猜 B 站接口的字段，这里直接告诉它
BILIBILI_FIELDS = [
    "bvid", "title", "author", "views", "danmaku", "likes",
    "coins", "favorites", "shares", "duration", "pubdate", "short_link",
]


class PlanError(ValueError):
    """AI 给出的计划不合格（结构错、列名不存在、用了不支持的环节）。"""


def _system_prompt(profile: Optional[Dict[str, Any]]) -> str:
    if profile and profile.get("fields"):
        columns = list(profile["fields"].keys())
        profile_text = (
            "本次数据已经存在，字段（列名）如下，只能使用这些列：\n"
            + json.dumps(columns, ensure_ascii=False)
            + "\n完整画像：\n"
            + json.dumps(profile, ensure_ascii=False)
        )
    else:
        profile_text = (
            "本次任务从接口采集数据，没有现成画像。B 站热门视频的字段为：\n"
            + ", ".join(BILIBILI_FIELDS)
        )

    return f"""你是一个数据分析管道的编排助手。用户会用中文描述需求，你要把它翻译成一份 JSON 管道计划。

可用的数据源（source.type 和 params）：
- bilibili_hot: {{"limit": 1~100}} 同步抓取 B 站热门视频
- bilibili_hot_async: {{"pages": 页数, "page_size": 每页条数, "concurrency": 并发数}} 并发抓取
- csv: {{"path": "文件路径"}}

可用的环节（stages[].type 和 params）：
- clean: {{"drop_na": true/false, "drop_duplicates": ["列名"], "strip_columns": ["列名"]}} 去空值/去重/去空格
- transform: {{"dtype_map": {{"列名": "int/float/str"}}, "derive": {{"新列名": "表达式"}}}} 表达式是 pandas.eval 语法，如 "likes / views"
- reduce: {{"group_by": "列名" 或 ["列名"], "agg": {{"列名": "sum/mean/max/min/count/nunique"}}}} 分组聚合；聚合后的列名保持原列名
- profile: 无参数，做一次数据体检
- duckdb: {{"table": "表名"}} 写入 DuckDB 中间库 data/warehouse.duckdb
- mysql: {{"table": "表名"}} 写入 MySQL
- file_sink: {{"path": "data/xxx_{{date}}.csv"}} 存 CSV，{{date}} 会替换成当天日期
- viz: {{"output": "output/xxx_{{date}}.html", "title": "标题", "charts": [{{"type": "bar_top/line/scatter", "x": "列名", "y": "列名", "top": 10, "title": "图标题"}}]}}

{profile_text}

规则：
1. 只输出 JSON，不要任何解释文字，不要 Markdown 代码块。
2. 严格使用这个结构：
{{"title": "计划标题", "reason": "一句话说明为什么这么设计", "source": {{"type": "...", "params": {{...}}}}, "stages": [{{"type": "...", "params": {{...}}}}]}}
3. 列名必须来自上面的字段列表，不要发明新列名。
4. reduce 之后，数据里只剩 group_by 的列和 agg 的列；后面的 viz 只能引用这些列。
5. 图表数量控制在 1~3 个，够用就好。
6. 需求里没有提到的环节不要乱加；同一个环节不要重复出现（例如不要连续两次 reduce）。
"""


def _user_prompt(instruction: str, extra_context: Optional[str]) -> str:
    text = f"用户需求：{instruction}"
    if extra_context:
        text += f"\n补充约束：{extra_context}"
    return text


def _check_columns(columns: Iterable[str], fields: set, where: str) -> None:
    """校验计划里引用的列确实存在。"""
    missing = [col for col in columns if fields and col not in fields]
    if missing:
        raise PlanError(
            f"{where} 引用了不存在的列 {missing}；当前可用列：{sorted(fields)}"
        )


def _validate_stage(spec: Any, index: int, fields: set) -> None:
    where = f"stages[{index}]"
    if not isinstance(spec, dict):
        raise PlanError(f"{where} 必须是对象")

    kind = spec.get("type")
    if kind not in STAGE_TYPES:
        raise PlanError(f"{where}.type 不支持: {kind}（可选 {sorted(STAGE_TYPES)}）")

    params = spec.get("params") or {}
    if not isinstance(params, dict):
        raise PlanError(f"{where}.params 必须是对象")

    if kind == "clean":
        dup = params.get("drop_duplicates")
        strip = params.get("strip_columns")
        if dup is not None:
            if not isinstance(dup, list):
                raise PlanError(f"{where}.params.drop_duplicates 必须是列表")
            _check_columns(dup, fields, where)
        if strip is not None:
            if not isinstance(strip, list):
                raise PlanError(f"{where}.params.strip_columns 必须是列表")
            _check_columns(strip, fields, where)

    elif kind == "transform":
        dtype_map = params.get("dtype_map") or {}
        derive = params.get("derive") or {}
        if not isinstance(dtype_map, dict) or not isinstance(derive, dict):
            raise PlanError(f"{where}.params.dtype_map / derive 必须是对象")
        _check_columns(dtype_map.keys(), fields, where)

    elif kind == "reduce":
        group_by = params.get("group_by")
        agg = params.get("agg")
        if isinstance(group_by, str):
            group_by = [group_by]
        if not isinstance(group_by, list) or not group_by:
            raise PlanError(f"{where}.params.group_by 必须是列名或非空列表")
        if not isinstance(agg, dict) or not agg:
            raise PlanError(f"{where}.params.agg 必须是非空对象")
        _check_columns(group_by + list(agg.keys()), fields, where)
        for func in agg.values():
            if not isinstance(func, str):
                raise PlanError(f"{where}.params.agg 的值必须是单个聚合函数名")

    elif kind in {"duckdb", "mysql"}:
        if not isinstance(params.get("table"), str) or not params["table"]:
            raise PlanError(f"{where}.params.table 必须是非空字符串")

    elif kind == "file_sink":
        if not isinstance(params.get("path"), str) or not params["path"]:
            raise PlanError(f"{where}.params.path 必须是非空字符串")

    elif kind == "viz":
        charts = params.get("charts")
        if not isinstance(charts, list) or not charts:
            raise PlanError(f"{where}.params.charts 必须是非空列表")
        for j, chart in enumerate(charts):
            if not isinstance(chart, dict):
                raise PlanError(f"{where}.params.charts[{j}] 必须是对象")
            chart_type = chart.get("type")
            if chart_type not in CHART_TYPES:
                raise PlanError(f"{where}.params.charts[{j}].type 不支持: {chart_type}")
            x, y = chart.get("x"), chart.get("y")
            if not x or not y:
                raise PlanError(f"{where}.params.charts[{j}] 需要 x 和 y")
            _check_columns([x, y], fields, f"{where}.params.charts[{j}]")


def validate_plan(plan: Any, profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """校验计划结构，不合格直接抛 PlanError（把问题说清楚，方便调提示词）。"""
    if not isinstance(plan, dict):
        raise PlanError("计划必须是 JSON 对象")

    source = plan.get("source")
    if not isinstance(source, dict):
        raise PlanError("缺少 source，或 source 不是对象")
    if source.get("type") not in SOURCE_TYPES:
        raise PlanError(
            f"source.type 不支持: {source.get('type')}（可选 {sorted(SOURCE_TYPES)}）"
        )
    source_params = source.get("params") or {}
    if not isinstance(source_params, dict):
        raise PlanError("source.params 必须是对象")
    if source["type"] == "csv" and not source_params.get("path"):
        raise PlanError("csv 数据源必须在 params.path 里给出文件路径")

    stages = plan.get("stages")
    if not isinstance(stages, list):
        raise PlanError("stages 必须是列表")

    fields = set((profile or {}).get("fields", {}).keys())
    for i, spec in enumerate(stages):
        _validate_stage(spec, i, fields)

    return plan


class Planner:
    """自然语言 → 校验过的管道计划。"""

    def __init__(self, client: Optional[LLMClient] = None) -> None:
        self.client = client or LLMClient()

    def plan(
        self,
        instruction: str,
        profile: Optional[Dict[str, Any]] = None,
        extra_context: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not instruction.strip():
            raise PlanError("需求描述不能为空")

        messages = [
            {"role": "system", "content": _system_prompt(profile)},
            {"role": "user", "content": _user_prompt(instruction, extra_context)},
        ]
        data = self.client.chat_json(messages, temperature=0.1, max_tokens=1024)

        # 有些模型会多包一层 {"plan": {...}}，这里顺手拆掉
        if "stages" not in data and isinstance(data.get("plan"), dict):
            data = data["plan"]

        data.setdefault("title", "AI Pipeline")
        data.setdefault("reason", "")
        data.setdefault("stages", [])
        return validate_plan(data, profile)
