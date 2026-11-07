"""可视化环节：把当前 DataFrame 渲染成一个 HTML 页面（图表+表格）。

它是管道里的普通 Stage：读 ctx.df，出图/出表写文件，不改变数据。

可视化用配置描述，不写死在代码里，AI 层直接生成这份配置：

    VizStage(
        output="output/bilibili_{date}.html",
        charts=[
            {"type": "bar_top", "x": "title", "y": "views", "top": 10},
            {"type": "scatter", "x": "views", "y": "likes"},
            {"type": "table", "columns": ["title", "views", "likes"], "title": "热门视频明细"},
        ],
    )

支持的 type：bar_top / line / scatter / table。
table 不需要 x/y，用 columns 指定展示哪些列（省略则展示全部）。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context
from . import charts


class VizStage(Stage):
    name = "viz"

    def __init__(
        self,
        output: str = "output/charts_{date}.html",
        charts: Optional[Sequence[Dict[str, Any]]] = None,
        title: str = "Data Pipeline Report",
    ) -> None:
        self.output = output
        self.charts: List[Dict[str, Any]] = list(charts or [])
        self.title = title

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        if df is None or df.empty:
            ctx.log("viz: 空数据，跳过")
            return df

        if not self.charts:
            ctx.log("viz: 没有配置可视化，跳过（charts=[]）")
            return df

        objects = [self._build(df, spec) for spec in self.charts]

        path = self.output.replace("{date}", datetime.now().strftime("%Y%m%d"))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        charts.render_page(objects, path, title=self.title)
        ctx.metadata["charts"] = path
        ctx.log(f"rendered {len(objects)} visualizations -> {path}")
        return df

    @staticmethod
    def _build(df: pd.DataFrame, spec: Dict[str, Any]):
        kind = spec.get("type", "bar_top")
        title = spec.get("title")

        # table 是纯数据展示，不需要 x/y
        if kind == "table":
            columns = spec.get("columns")
            return charts.render_table(
                df,
                columns=columns,
                title=title,
                max_rows=spec.get("max_rows", 200),
            )

        x = spec["x"]
        y = spec["y"]

        # 缺列时给出明确提示，而不是让 pandas 抛一个看不懂的 KeyError
        missing = [col for col in (x, y) if col not in df.columns]
        if missing:
            raise ValueError(f"图表配置引用了不存在的列: {missing}")

        if kind == "bar_top":
            return charts.bar_top(df, x, y, top=spec.get("top", 10), title=title)
        if kind == "line":
            return charts.line(df, x, y, title=title)
        if kind == "scatter":
            return charts.scatter(df, x, y, title=title)

        raise ValueError(f"不支持的可视化类型: {kind}（可选 bar_top / line / scatter / table）")
