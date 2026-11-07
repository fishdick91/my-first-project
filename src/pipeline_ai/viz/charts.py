"""图表/表格生成函数。

这里只负责"把 DataFrame 变成可视化对象"，不做业务判断。
图表的类型、用哪两列，由调用方（VizStage 或 AI 层）决定。

支持：
    bar_top    横向柱状图，取某个指标的 Top N
    line       折线图，按 X 排序后看趋势
    scatter    散点图，看两个指标的相关性
    table      数据表，直接把 DataFrame 渲染成 HTML 表格
"""

from __future__ import annotations

import html
import os
import re
from typing import List, Optional, Sequence

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Scatter

# pyecharts 默认从 CDN 加载 echarts.js，每次打开 HTML 都要联网，很慢。
# 这里把 echarts.min.js 下载到项目的 assets/ 目录，渲染时内联进 HTML，
# 生成的文件完全自包含，打开秒开、离线也能用。
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets")
_ECHARTS_JS_PATH = os.path.join(_ASSETS_DIR, "echarts.min.js")
_ECHARTS_CACHE: Optional[str] = None


def _load_echarts_js() -> str:
    """读取本地 echarts.min.js，缓存起来避免每次渲染都读磁盘。"""
    global _ECHARTS_CACHE
    if _ECHARTS_CACHE is None:
        with open(_ECHARTS_JS_PATH, "r", encoding="utf-8") as f:
            _ECHARTS_CACHE = f.read()
    return _ECHARTS_CACHE


def bar_top(
    df: pd.DataFrame,
    x: str,
    y: str,
    top: int = 10,
    title: Optional[str] = None,
) -> Bar:
    """按 y 降序取前 top 条，画成横向柱状图。"""
    data = df[[x, y]].dropna().nlargest(top, y)
    # 横向柱状图从下往上绘制，倒序让最大值出现在顶部
    data = data.iloc[::-1]

    bar = Bar(init_opts=opts.InitOpts(width="100%", height="420px"))
    bar.add_xaxis([str(v) for v in data[x]])
    bar.add_yaxis(y, data[y].tolist())
    bar.reversal_axis()
    bar.set_global_opts(
        title_opts=opts.TitleOpts(title=title or f"{y} Top {top}"),
        xaxis_opts=opts.AxisOpts(name=y),
        yaxis_opts=opts.AxisOpts(name=x),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
    )
    return bar


def line(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: Optional[str] = None,
) -> Line:
    """按 x 排序画折线图，用来看趋势。"""
    data = df[[x, y]].dropna().sort_values(x)

    chart = Line(init_opts=opts.InitOpts(width="100%", height="360px"))
    chart.add_xaxis([str(v) for v in data[x]])
    chart.add_yaxis(y, data[y].tolist(), is_smooth=True)
    chart.set_global_opts(
        title_opts=opts.TitleOpts(title=title or f"{y} by {x}"),
        xaxis_opts=opts.AxisOpts(name=x),
        yaxis_opts=opts.AxisOpts(name=y),
        tooltip_opts=opts.TooltipOpts(trigger="axis"),
    )
    return chart


def scatter(
    df: pd.DataFrame,
    x: str,
    y: str,
    title: Optional[str] = None,
) -> Scatter:
    """两个数值指标的关系，比如播放量 vs 点赞数。"""
    data = df[[x, y]].dropna()

    chart = Scatter(init_opts=opts.InitOpts(width="100%", height="420px"))
    chart.add_xaxis(data[x].tolist())
    chart.add_yaxis(y, data[y].tolist(), symbol_size=9)
    chart.set_global_opts(
        title_opts=opts.TitleOpts(title=title or f"{x} vs {y}"),
        xaxis_opts=opts.AxisOpts(type_="value", name=x),
        yaxis_opts=opts.AxisOpts(type_="value", name=y),
        tooltip_opts=opts.TooltipOpts(trigger="item"),
    )
    return chart


def render_table(
    df: pd.DataFrame,
    columns: Optional[Sequence[str]] = None,
    title: Optional[str] = None,
    max_rows: int = 200,
) -> str:
    """把 DataFrame 渲染成一个带样式的 HTML 表格片段。

    columns: 只展示这些列，None 表示全部列。
    max_rows: 最多展示多少行，避免 HTML 过大。
    返回的是 <div>...</div> 片段，不含 <html>/<body>，可直接拼进页面。
    """
    data = df.copy()
    if columns:
        data = data[[c for c in columns if c in data.columns]]
    data = data.head(max_rows)

    thead = "".join(f"<th>{html.escape(str(c))}</th>" for c in data.columns)
    rows = []
    for _, row in data.iterrows():
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in row.values)
        rows.append(f"<tr>{cells}</tr>")
    tbody = "\n".join(rows)

    title_html = f"<h3 style='margin:16px 0 8px'>{html.escape(title or '数据表')}</h3>" if title else ""
    caption = f"<p style='color:#888;font-size:12px;margin:4px 0'>共 {len(df)} 行，展示前 {len(data)} 行</p>"

    return f"""<div class="table-block">
{title_html}
<table style="border-collapse:collapse;width:100%;font-size:13px;">
<thead><tr style="background:#f5f5f5;">{thead}</tr></thead>
<tbody>{tbody}</tbody>
</table>
{caption}
</div>
<style>
.table-block th, .table-block td {{
  border:1px solid #e0e0e0; padding:6px 10px; text-align:left;
}}
.table-block tbody tr:nth-child(even) {{ background:#fafafa; }}
.table-block tbody tr:hover {{ background:#eef6ff; }}
</style>
"""


def render_page(
    items: Sequence[object],
    output_path: str,
    title: str = "Data Pipeline Report",
) -> str:
    """把多个可视化对象拼成一个 HTML 页面，写到 output_path。

    items 里可以混合：
      - pyecharts 图表对象（Bar / Line / Scatter 等）→ 用 render_embed() 拿片段
      - 字符串（render_table 返回的 HTML 片段）→ 直接拼
    渲染后会把 echarts.js 内联进 HTML，让文件自包含、离线秒开。
    """
    fragments: List[str] = []
    for item in items:
        if isinstance(item, str):
            fragments.append(item)
        else:
            # pyecharts 图表对象：render_embed() 返回不含 <html>/<body> 的片段
            fragments.append(item.render_embed())

    body = "\n".join(fragments)
    page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
</head>
<body>
{body}
</body>
</html>
"""

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # 内联 echarts.js（图表片段里会引用）
    if os.path.exists(_ECHARTS_JS_PATH):
        echarts_js = _load_echarts_js()
        page_html = page_html.replace("</head>", f"<script>{echarts_js}</script></head>", 1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    return output_path
