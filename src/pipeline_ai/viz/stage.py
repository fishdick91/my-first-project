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
column_aliases 只用于渲染（表头、坐标轴），不会改变管道中的 DataFrame。
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
        column_aliases: Optional[Dict[str, str]] = None,
    ) -> None:
        self.output = output
        self.charts: List[Dict[str, Any]] = list(charts or [])
        self.title = title
        # 仅用于渲染的展示别名，管道里的 DataFrame 始终保持原始列名
        self.column_aliases = dict(column_aliases or {})

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        if df is None or df.empty:
            ctx.log("viz: 空数据，跳过")
            return df

        if not self.charts:
            ctx.log("viz: 没有配置可视化，跳过（charts=[]）")
            return df

        # 渲染副本用别名，同时把图表配置里的列引用一起换掉，
        # 否则 render_table 会把「不存在的列」静默过滤成 0 列空表。
        render_df = df.rename(columns=self.column_aliases) if self.column_aliases else df
        specs = [self._apply_aliases(spec) for spec in self.charts]
        # 列名只有在数据到手后才可信（web 源由 AI 现场命名），用运行时 df 校正计划
        specs = [self._reconcile(render_df, spec, ctx) for spec in specs]
        objects = [self._build(render_df, spec) for spec in specs]

        path = self.output.replace("{date}", datetime.now().strftime("%Y%m%d"))
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        meta = {
            "rows": len(df),
            "columns": len(render_df.columns),
            "fields": list(render_df.columns),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        charts.render_page(objects, path, title=self.title, meta=meta)
        ctx.metadata["charts"] = path
        ctx.metadata["viz_kinds"] = [spec.get("type", "table") for spec in specs]
        ctx.metadata["viz_rows"] = len(df)
        ctx.metadata["viz_columns"] = len(render_df.columns)
        ctx.log(f"rendered {len(objects)} visualizations -> {path}")
        return df

    @staticmethod
    def _reconcile(df: pd.DataFrame, spec: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
        """用运行时真实列名校正计划里的列引用。

        web / 动态字段源的列名是爬取时由 AI 现场确定的，Planner 只能猜。
        猜错时绝不能渲染出空表：表格回退展示全部列、图表降级为数据表，
        并写进执行日志与 metadata，方便 UI 提示「已自动校正」。
        """
        out = dict(spec)
        kind = out.get("type", "table")
        available = list(df.columns)

        def warn(message: str) -> None:
            ctx.log(f"⚠️ {message}")
            ctx.metadata.setdefault("viz_warnings", []).append(message)

        if kind == "table":
            cols = out.get("columns")
            if cols:
                missing = [c for c in cols if c not in df.columns]
                if missing:
                    warn(f"viz: 表格列 {missing} 不存在，回退展示全部 {len(available)} 列")
                    out.pop("columns", None)
            return out

        x, y = out.get("x"), out.get("y")
        missing = [c for c in (x, y) if c not in df.columns]
        if missing:
            warn(f"viz: {kind} 引用的列 {missing} 不存在，降级为数据表")
            return {"type": "table", "title": out.get("title")}
        return out

    def _apply_aliases(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """把图表配置里的 x / y / columns 换成展示别名。"""
        if not self.column_aliases:
            return spec
        out = dict(spec)
        for key in ("x", "y"):
            if isinstance(out.get(key), str):
                out[key] = self.column_aliases.get(out[key], out[key])
        if isinstance(out.get("columns"), list):
            out["columns"] = [
                self.column_aliases.get(col, col)
                for col in out["columns"]
                if isinstance(col, str)
            ]
        return out

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
