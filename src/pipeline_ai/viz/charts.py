"""图表生成函数。

这里只负责"把 DataFrame 变成 pyecharts 图表对象"，不做业务判断。
图表的类型、用哪两列，由调用方（VizStage 或以后的 AI 层）决定。

支持三种图：
    bar_top    横向柱状图，取某个指标的 Top N
    line       折线图，按 X 排序后看趋势
    scatter    散点图，看两个指标的相关性
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence

import pandas as pd
from pyecharts import options as opts
from pyecharts.charts import Bar, Line, Page, Scatter

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


def render_page(
    charts: Sequence[object],
    output_path: str,
    title: str = "Data Pipeline Report",
) -> str:
    """把多张图拼成一个 HTML 页面，写到 output_path。

    渲染后会把 echarts.js 内联进 HTML，让文件自包含、离线秒开。
    """
    page = Page(layout=Page.SimplePageLayout, page_title=title)
    for chart in charts:
        page.add(chart)
    page.render(output_path)

    # 把 CDN 的 <script src="...echarts.min.js"> 替换成本地内联脚本
    with open(output_path, "r", encoding="utf-8") as f:
        html = f.read()

    if os.path.exists(_ECHARTS_JS_PATH):
        echarts_js = _load_echarts_js()
        # 用函数做替换，避免 echarts.js 里的反斜杠被 re 当成转义符
        html = re.sub(
            r'<script[^>]*src="[^"]*echarts[^"]*\.js"[^>]*>\s*</script>',
            lambda _m: f"<script>{echarts_js}</script>",
            html,
            count=1,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    return output_path
