"""列名映射：把数据源字段名翻译成用户想要的名字。

设计约定（重要）：
    管道内部始终使用数据源的原始列名（契约稳定，计划/校验/聚合都基于它）；
    用户别名只在「展示与导出」时生效 —— VizStage 渲染副本用别名，
    管道末尾的 RenameColumnsStage 把最终 DataFrame 改成用户字段名。

映射在 build_pipeline 组装期解析一次，同时喂给 VizStage 和末尾的
RenameColumnsStage，两边用的是同一份 mapping，因此不会出现
「数据改名了、计划里还引用旧列名」的错位。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


RENAME_PROMPT = """你是一个数据列名映射助手。我会给你：
1. 用户的需求描述
2. 当前 DataFrame 的列名列表

请把列名映射成用户需求里提到的字段名。只输出 JSON 对象，key 是原列名，value 是新列名。
如果某个列用户没有要求，就保持原名（key 和 value 相同）。
如果用户没有明确指定字段名，就不做任何映射，输出空对象 {{}}。

用户需求：{instruction}
当前列名：{columns}

输出格式示例：
{{"title": "电影名", "rating": "评分", "director": "导演"}}
"""


def sanitize_mapping(mapping: Any, columns: Iterable[str]) -> Dict[str, str]:
    """清洗模型给出的映射，挡掉不存在的列、空名和会撞名的情况。

    df.rename 允许产生重复列名，但那会让后续 group_by / 图表全部出问题，
    所以这里必须在应用前拦住。
    """
    if not isinstance(mapping, dict):
        return {}

    known = set(columns)
    used: set = set()
    result: Dict[str, str] = {}
    for old, new in mapping.items():
        if old not in known or not isinstance(new, str) or not new.strip():
            continue
        new = new.strip()
        # 与自身相同、与另一个原始列撞名、或多个旧列映射到同一新名
        if new == old or new in known or new in used:
            continue
        result[old] = new
        used.add(new)
    return result


def resolve_column_aliases(
    instruction: Optional[str],
    columns: Iterable[str],
    client: Optional[Any] = None,
) -> Dict[str, str]:
    """用 AI 把预期列名映射成用户描述的字段名，返回 {原列名: 新列名}。

    调用方负责捕获异常并降级为空映射 —— 别名只是展示锦上添花，
    不该因为模型抖动让整条管道跑不起来。
    """
    columns = list(columns)
    if not instruction or not columns:
        return {}

    from ..ai.llm import LLMClient, extract_json

    client = client or LLMClient(timeout=60)
    prompt = RENAME_PROMPT.format(
        instruction=instruction,
        columns=json.dumps(columns, ensure_ascii=False),
    )
    messages = [
        {"role": "system", "content": "你是列名映射助手，只输出 JSON。"},
        {"role": "user", "content": prompt},
    ]
    text = client.chat(messages, temperature=0.0, max_tokens=512)
    return sanitize_mapping(extract_json(text), columns)


class RenameColumnsStage(Stage):
    name = "rename_columns"

    def __init__(
        self,
        mapping: Optional[Dict[str, str]] = None,
        instruction: Optional[str] = None,
    ) -> None:
        # mapping 优先（组装期已解析好，不再调 LLM）
        self.mapping = dict(mapping or {})
        # 保留 instruction 兼容旧的直接构造用法，此时在运行时自行解析
        self.instruction = instruction

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        if df is None or df.empty:
            return df

        mapping = sanitize_mapping(self.mapping, df.columns)
        if not mapping and self.instruction:
            try:
                mapping = resolve_column_aliases(self.instruction, list(df.columns))
            except Exception as exc:
                ctx.log(f"⚠️ 列名映射失败，保持原列名: {exc}")
                return df

        if not mapping:
            ctx.log("无需列名映射")
            return df

        ctx.metadata["column_aliases"] = mapping
        ctx.log(f"列名映射: {mapping}")
        return df.rename(columns=mapping)
