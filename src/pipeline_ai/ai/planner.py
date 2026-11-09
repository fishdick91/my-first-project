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
import re
from typing import Any, Dict, Iterable, List, Optional

from .llm import LLMClient

# 数据源类型从注册表动态获取（加上 csv 这种本地源）
def _get_source_types() -> set:
    from ..sources.registry import list_sources
    return set(list_sources()) | {"csv", "bilibili_hot_async"}

SOURCE_TYPES = _get_source_types()
STAGE_TYPES = {"clean", "transform", "reduce", "profile", "duckdb", "mysql", "file_sink", "viz"}
CHART_TYPES = {"bar_top", "line", "scatter", "table"}

# 没有数据画像时，模型只能猜接口字段，这里直接告诉它
BILIBILI_FIELDS = [
    "bvid", "title", "author", "views", "danmaku", "likes",
    "coins", "favorites", "shares", "duration", "pubdate", "short_link",
]
DOUBAN_FIELDS = ["title", "director", "actors", "quote", "rating"]


def _build_source_prompt() -> str:
    """从注册表动态生成数据源说明，新增源自动出现在提示词里。"""
    from ..sources.registry import list_sources, get_source_config, get_fields, is_catch_all, get_search_url

    lines = []
    for src_type in list_sources():
        cfg = get_source_config(src_type)
        label = cfg.get("label", src_type)
        overridable = cfg.get("params_overridable", {})
        search_url = get_search_url(src_type)

        # 生成 params 说明
        param_parts = []
        for pname, pcfg in overridable.items():
            pdefault = pcfg.get("default", "")
            pmin = pcfg.get("min", "")
            pmax = pcfg.get("max", "")
            if pmin or pmax:
                param_parts.append(f'"{pname}": {pmin}~{pmax}（默认{pdefault}）')
            else:
                param_parts.append(f'"{pname}": {pdefault}' if not isinstance(pdefault, str) else f'"{pname}": "{pdefault}"')

        # web 源支持 keyword 搜索
        if cfg.get("fetcher") == "web" and search_url:
            param_parts.append('"keyword": "搜索关键词（可选）"')

        params_str = ", ".join(param_parts) if param_parts else "无"

        # 字段说明
        if cfg.get("fetcher") == "web":
            fields_desc = "**网页自动分析**（爬取时 AI 自动识别页面内容，可根据用户需求提取任意字段）"
        elif is_catch_all(src_type):
            fields_desc = "**全部字段**（探测时自动获取，可根据用户需求选择任意列）"
        else:
            fields_desc = ", ".join(get_fields(src_type).keys())

        # 搜索能力说明
        search_note = ""
        if search_url:
            search_note = f"。**支持搜索**：用户提到具体内容/话题时，把关键词放到 params.keyword，系统会自动用搜索 URL"

        # 特殊说明
        note = ""
        if src_type == "bilibili_search":
            note = '。**只要用户提到具体话题/关键词，或出现"搜索/查找/找一下"等词，必须用这个源**'
        if src_type == "douban_top":
            note = '。**如果用户提到"评论"、"短评"、"评语"，with_review 必须设为 true**'

        lines.append(f"- {src_type}: {{{params_str}}} — {label}，字段：{fields_desc}{search_note}{note}")

    lines.append('- csv: {"path": "文件路径"} — 本地 CSV 文件')
    lines.append('- web: {"url": "网页地址", "keyword": "搜索关键词（可选）"} — **通用网页爬虫**，输入任意网址，AI 自动分析页面提取数据，无需预先配置。当用户提供了具体网址，或想爬的网站不在上面列表里时，用这个源。如果用户提到具体内容，把关键词放到 keyword。')
    return "\n".join(lines)


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
            "本次任务从接口采集数据，没有现成画像。\n"
            f"B 站热门/搜索视频字段：{', '.join(BILIBILI_FIELDS)}\n"
            f"豆瓣 Top 电影字段：{', '.join(DOUBAN_FIELDS)}"
        )

    source_prompt = _build_source_prompt()

    return f"""你是一个数据分析管道的编排助手。用户会用中文描述需求，你要把它翻译成一份 JSON 管道计划。

可用的数据源（source.type 和 params）：
{source_prompt}

**数据源由你根据用户描述自动选择**，用户不会手动指定 source.type。你需要从用户的话里判断该用哪个源。

可用的环节（stages[].type 和 params）：
- clean: {{"drop_na": true/false, "drop_duplicates": ["列名"], "strip_columns": ["列名"]}} 去空值/去重/去空格
- transform: {{"dtype_map": {{"列名": "int/float/str"}}, "derive": {{"新列名": "表达式"}}}} 表达式是 pandas.eval 语法，如 "likes / views"
- reduce: {{"group_by": "列名" 或 ["列名"], "agg": {{"列名": "sum/mean/max/min/count/nunique"}}}} 分组聚合。**group_by 不能为 null，必须是实际存在的列名；agg 里每个列名只能出现一次，不能重复**。聚合后的列名保持原列名。**不要用 reduce 来"取前几条评论"，评论是数据源直接提供的字段**。
- profile: 无参数，做一次数据体检
- duckdb: {{"table": "表名"}} 写入 DuckDB 中间库 data/warehouse.duckdb
- mysql: {{"table": "表名"}} 写入 MySQL
- file_sink: {{"path": "data/xxx_{{date}}.csv"}} 存 CSV，{{date}} 会替换成当天日期
- viz: {{"output": "output/xxx_{{date}}.html", "title": "标题", "charts": [图表配置]}}

**可视化类型（charts[].type）—— 必须严格按用户指定的呈现形式来选：**
- table: 数据表。配置：{{"type": "table", "columns": ["列1","列2"], "title": "表标题", "max_rows": 200}}。**不需要 x/y**。当用户说"表格"、"明细表"、"列表"、"数据表"时用这个。columns 省略则展示全部列。
- bar_top: 横向柱状图（排行）。配置：{{"type": "bar_top", "x": "名称列", "y": "数值列", "top": 10, "title": "..."}}。当用户说"柱状图"、"排行"、"Top"时用这个。
- line: 折线图。配置：{{"type": "line", "x": "列", "y": "列", "title": "..."}}。当用户说"折线图"、"趋势"时用这个。
- scatter: 散点图。配置：{{"type": "scatter", "x": "列", "y": "列", "title": "..."}}。当用户说"散点图"、"相关性"时用这个。

{profile_text}

规则：
1. 只输出 JSON，不要任何解释文字，不要 Markdown 代码块。
2. 严格使用这个结构：
{{"title": "计划标题", "reason": "一句话说明为什么这么设计", "source": {{"type": "...", "params": {{...}}}}, "stages": [{{"type": "...", "params": {{...}}}}]}}
3. 列名必须来自上面的字段列表，不要发明新列名。
4. reduce 之后，数据里只剩 group_by 的列和 agg 的列；后面的 viz 只能引用这些列。
5. 可视化数量控制在 1~3 个，够用就好。**用户指定了呈现形式就必须用那个形式**，不要自作主张换类型。
6. 需求里没有提到的环节不要乱加；同一个环节不要重复出现。
7. bar_top 本身就会按 y 排序取前 N 条，不需要提前 reduce。画排行柱状图时 x 轴用 title（名称），这样标签可读。
8. **排序不需要 reduce！** 用户说"按某列排序"时，直接在 viz 的 columns 里指定列即可，数据源本身已有顺序。**绝对不要加 reduce 环节来排序，group_by 绝不能为 null**。
9. **不要用 reduce 处理评论**。用户要评论时，设 douban_top 的 with_review=true，然后在 viz 的 columns 里加上 quote 字段即可。

示例（只看格式和 source 选择，不要照抄内容）：
- 需求："抓取 B 站热门视频，按播放量画 Top10 柱状图" → source: {{"type": "bilibili_hot", "params": {{"limit": 30}}}}, viz 用 bar_top
- 需求："搜索 B 站 vibe coding 视频，做成表格展示标题和播放量" → source: {{"type": "bilibili_search", "params": {{"keyword": "vibe coding", "limit": 50}}}}, viz 用 table
- 需求："爬取豆瓣top前50电影，用表格展示电影名、评分、导演，按评分从高到低" → source: {{"type": "douban_top", "params": {{"limit": 50}}}}, **只有一个 viz 环节，不要 reduce**，table columns=["title","rating","director"]
- 需求："B站热门视频，按播放量排序，用表格展示" → source: {{"type": "bilibili_hot", "params": {{"limit": 20}}}}, **只有一个 viz 环节，不要 reduce**，table columns=["title","views","author"]
- 需求："豆瓣电影前25名，展示电影名、评分、导演、演员、评论" → source: {{"type": "douban_top", "params": {{"limit": 25, "with_review": true}}}}, stages 里**不要 reduce**，直接 viz 用 table columns=["title","rating","director","actors","quote"]
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


def _validate_stage(spec: Any, index: int, fields: set) -> set:
    """校验单个环节，并返回执行后还剩哪些列。

    关键：reduce 会把列裁成"group_by 的列 + agg 的列"，
    后续环节只能用这些列。这里模拟一遍列的流转，
    把 AI 那种"先 reduce 掉 title、后面又要用 title"的矛盾拦住。
    """
    where = f"stages[{index}]"
    if not isinstance(spec, dict):
        raise PlanError(f"{where} 必须是对象")

    kind = spec.get("type")
    if kind not in STAGE_TYPES:
        raise PlanError(f"{where}.type 不支持: {kind}（可选 {sorted(STAGE_TYPES)}）")

    params = spec.get("params") or {}
    if not isinstance(params, dict):
        raise PlanError(f"{where}.params 必须是对象")

    # 默认大多数环节不改变列集合
    next_fields = set(fields)

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
        # derive 会新增列
        next_fields |= set(derive.keys())

    elif kind == "reduce":
        group_by = params.get("group_by")
        agg = params.get("agg")
        # 容错：group_by 为 None 或 agg 为空，视为无效 reduce，跳过（不改变列）
        if group_by is None or not agg:
            return next_fields
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
        # reduce 之后只剩 group_by 的列和 agg 的列
        next_fields = set(group_by) | set(agg.keys())

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
            # table 不需要 x/y，用 columns（可选）
            if chart_type == "table":
                cols = chart.get("columns")
                if cols is not None:
                    if not isinstance(cols, list):
                        raise PlanError(f"{where}.params.charts[{j}].columns 必须是列表")
                    _check_columns(cols, fields, f"{where}.params.charts[{j}]")
            else:
                x, y = chart.get("x"), chart.get("y")
                if not x or not y:
                    raise PlanError(f"{where}.params.charts[{j}] 需要 x 和 y")
                _check_columns([x, y], fields, f"{where}.params.charts[{j}]")

    return next_fields


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
    if source["type"] == "bilibili_search" and not source_params.get("keyword"):
        raise PlanError("bilibili_search 数据源必须在 params.keyword 里给出搜索关键词")

    stages = plan.get("stages")
    if not isinstance(stages, list):
        raise PlanError("stages 必须是列表")

    # 初始列：有画像用画像，B 站接口用已知字段，否则空集（csv 无画像时不检查列）
    profile_fields = set((profile or {}).get("fields", {}).keys())
    if profile_fields:
        fields = profile_fields
    elif source["type"] in {"bilibili_hot", "bilibili_search", "bilibili_hot_async"}:
        fields = set(BILIBILI_FIELDS)
    else:
        fields = set()

    for i, spec in enumerate(stages):
        fields = _validate_stage(spec, i, fields)

    return plan


class Planner:
    """自然语言 → 校验过的管道计划。"""

    # 出现这些词说明用户想"搜索"，而不是看热门榜
    _SEARCH_HINTS = ("搜索", "查找", "找一下", "搜一下", "搜", "找")

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

        # 生成 → 校验，校验不过就把错误喂回给模型让它改，最多重试 3 次。
        last_error: Optional[PlanError] = None
        for attempt in range(3):
            data = self.client.chat_json(messages, temperature=0.0, max_tokens=1024)

            if "stages" not in data and isinstance(data.get("plan"), dict):
                data = data["plan"]

            data.setdefault("title", "AI Pipeline")
            data.setdefault("reason", "")
            data.setdefault("stages", [])

            # 清理无效 stage：跳过 group_by=None 的 reduce、charts 为空的 viz
            cleaned_stages = []
            for spec in data.get("stages", []):
                if not isinstance(spec, dict):
                    continue
                kind = spec.get("type")
                params = spec.get("params") or {}
                if kind == "reduce":
                    if params.get("group_by") is None or not params.get("agg"):
                        continue
                if kind == "viz":
                    if not params.get("charts"):
                        continue
                cleaned_stages.append(spec)
            data["stages"] = cleaned_stages

            if self._looks_like_search(instruction):
                source = data.get("source") or {}
                if source.get("type") in {"bilibili_hot", "bilibili_hot_async"}:
                    keyword = self._extract_keyword(instruction)
                    if keyword:
                        data["source"] = {
                            "type": "bilibili_search",
                            "params": {"keyword": keyword, "limit": 50},
                        }

            try:
                return validate_plan(data, profile)
            except PlanError as exc:
                last_error = exc
                messages.append({"role": "assistant", "content": json.dumps(data, ensure_ascii=False)})
                messages.append({
                    "role": "user",
                    "content": (
                        f"这个计划校验不通过：{exc}\n"
                        "请修正后只输出新的 JSON，不要解释。"
                        "提示：reduce 会把列裁成 group_by 的列加 agg 的列，"
                        "后续环节只能用这些列；画排行图通常不需要 reduce，"
                        "直接用 viz.bar_top 即可。"
                    ),
                })

        raise last_error  # 重试到上限仍不通过，抛出最后一次的错误

    @classmethod
    def _looks_like_search(cls, instruction: str) -> bool:
        return any(hint in instruction for hint in cls._SEARCH_HINTS)

    @staticmethod
    def _extract_keyword(instruction: str) -> str:
        """从指令里粗略提取搜索关键词。

        去掉"搜索/查找/找一下/B站/视频/的"等词和标点，剩下的作为关键词。
        提取不准也没关系，B 站搜索容错性强。
        """
        text = instruction
        for word in [
            "搜索", "查找", "找一下", "搜一下", "搜", "找",
            "B站", "bilibili", "B 站", "视频", "的", "一下",
        ]:
            text = text.replace(word, " ")
        # 去掉"按xxx"、"画xxx"、"取xxx"这类分析意图
        text = re.split(r"[，,。.；;]", text)[0]
        text = re.sub(r"(按|画|取|统计|汇总|看|分析).*$", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text or instruction
