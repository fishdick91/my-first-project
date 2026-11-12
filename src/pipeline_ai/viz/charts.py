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

    注意：列名对不上时直接抛错，绝不能静默过滤成 0 列空表 —— 之前正是
    这行 `[c for c in columns if c in data.columns]` 让空表伪装成执行成功。
    """
    data = df.copy()
    if columns is not None:
        columns = list(columns)
        missing = [c for c in columns if c not in data.columns]
        if missing:
            raise ValueError(
                f"表格配置引用了不存在的列: {missing}；可用列: {list(data.columns)}"
            )
        if not columns:
            raise ValueError("表格 columns 为空，无法确定要展示哪些列")
        data = data[columns]
    if data.shape[1] == 0:
        raise ValueError("数据没有任何列，拒绝生成空表")
    data = data.head(max_rows)

    thead = "".join(f"<th>{html.escape(str(c))}</th>" for c in data.columns)
    rows = []
    for _, row in data.iterrows():
        cells = "".join(f"<td>{html.escape(str(v))}</td>" for v in row.values)
        rows.append(f"<tr>{cells}</tr>")
    tbody = "\n".join(rows)

    title_html = f"<h3 style='margin:16px 0 8px'>{html.escape(title or '数据表')}</h3>" if title else ""
    caption = (
        f"<p style='color:#888;font-size:12px;margin:4px 0'>"
        f"共 {len(df)} 行 × {len(data.columns)} 列，展示前 {len(data)} 行</p>"
    )

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


_REPORT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin:0; padding:32px 20px 56px; background:#f6f7f9; color:#1f2329;
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
.report { max-width:1120px; margin:0 auto; }
.report-head { background:#fff; border:1px solid #e5e7eb; border-radius:12px;
               padding:20px 24px; margin-bottom:16px; }
.report-head h1 { margin:0 0 10px; font-size:21px; font-weight:500; }
.report-meta { display:flex; flex-wrap:wrap; gap:8px 18px; color:#6b7280; font-size:13px; }
.report-meta b { color:#1f2329; font-weight:500; }
.report-fields { margin-top:12px; display:flex; flex-wrap:wrap; gap:6px; }
.field-chip { background:#f1f3f6; border-radius:6px; padding:2px 9px; font-size:12px; color:#4b5563; }
.report-body { background:#fff; border:1px solid #e5e7eb; border-radius:12px;
               padding:8px 24px 24px; overflow-x:auto; }
.report-body > div { margin-top:16px; }
.report-foot { margin-top:14px; color:#9ca3af; font-size:12px; text-align:center; }
"""


def _render_meta(meta: Optional[dict]) -> str:
    """生成报告摘要头，让结果脱离聊天窗口也能自解释。"""
    meta = meta or {}
    bits = []
    if meta.get("source"):
        bits.append(f"数据源 <b>{html.escape(str(meta['source']))}</b>")
    if meta.get("rows") is not None:
        bits.append(f"共 <b>{meta['rows']}</b> 行")
    if meta.get("columns") is not None:
        bits.append(f"<b>{meta['columns']}</b> 列")
    if meta.get("generated_at"):
        bits.append(f"生成于 <b>{html.escape(str(meta['generated_at']))}</b>")
    meta_line = " · ".join(bits) if bits else "AI 数据管道生成"

    fields = list(meta.get("fields") or [])
    chips = "".join(
        f'<span class="field-chip">{html.escape(str(c))}</span>' for c in fields[:8]
    )
    if len(fields) > 8:
        chips += f'<span class="field-chip">+{len(fields) - 8}</span>'
    fields_html = f'<div class="report-fields">{chips}</div>' if chips else ""

    return (
        '<div class="report-meta">' + meta_line + "</div>" + fields_html
    )


def render_page(
    items: Sequence[object],
    output_path: str,
    title: str = "Data Pipeline Report",
    meta: Optional[dict] = None,
) -> str:
    """把多个可视化对象拼成一个自包含 HTML 报告，写到 output_path。

    items 里可以混合：
      - pyecharts 图表对象（Bar / Line / Scatter 等）→ 用 render_embed() 拿片段
      - 字符串（render_table 返回的 HTML 片段）→ 直接拼

    echarts.js 只在确实有图表对象时才内联：纯表格报告不必背 1 MB 的 JS。
    meta 提供摘要信息（rows / columns / fields / source / generated_at）。
    """
    fragments: List[str] = []
    has_charts = False
    for item in items:
        if isinstance(item, str):
            fragments.append(item)
        else:
            # pyecharts 图表对象：render_embed() 返回不含 <html>/<body> 的片段
            has_charts = True
            fragments.append(item.render_embed())

    body = "\n".join(fragments)
    page_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_REPORT_CSS}</style>
</head>
<body>
<div class="report">
<header class="report-head">
<h1>{html.escape(title)}</h1>
{_render_meta(meta)}
</header>
<div class="report-body">
{body}
</div>
<footer class="report-foot">AI 数据管道 · 自包含离线报告</footer>
</div>
</body>
</html>
"""

    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    # 只有图表片段会引用 echarts，纯表格报告保持轻量
    if has_charts and os.path.exists(_ECHARTS_JS_PATH):
        echarts_js = _load_echarts_js()
        page_html = page_html.replace("</head>", f"<script>{echarts_js}</script></head>", 1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(page_html)

    return output_path
