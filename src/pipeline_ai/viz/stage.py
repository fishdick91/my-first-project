"""可视化环节：把当前 DataFrame 渲染成一个 HTML 图表页。

它是管道里的普通 Stage：读 ctx.df，出图写文件，不改变数据。

图表用配置描述，不写死在代码里，以后 AI 层可以直接生成这份配置：

    VizStage(
        output="output/bilibili_{date}.html",
        charts=[
            {"type": "bar_top", "x": "title", "y": "views", "top": 10},
            {"type": "scatter", "x": "views", "y": "likes"},
            {"type": "line", "x": "pubdate", "y": "views"},
        ],
    )

支持的 type：bar_top / line / scatter。
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
            ctx.log("viz: 没有配置图表，跳过（charts=[]）")
            return df

        objects = [self._build(df, spec) for spec in self.charts]

        path = self.output.replace("{date}", datetime.now().strftime("%Y%m%d"))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        charts.render_page(objects, path, title=self.title)
        ctx.metadata["charts"] = path
        ctx.log(f"rendered {len(objects)} charts -> {path}")
        return df

    @staticmethod
    def _build(df: pd.DataFrame, spec: Dict[str, Any]):
        kind = spec.get("type", "bar_top")
        x = spec["x"]
        y = spec["y"]
        title = spec.get("title")

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

        raise ValueError(f"不支持的图表类型: {kind}（可选 bar_top / line / scatter）")
