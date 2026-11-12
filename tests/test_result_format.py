"""默认结果格式与运行时列名契约的回归测试（不调 LLM、不联网）。

锁死两类 bug：
1. web / 动态字段源的列名要爬完才知道，计划里猜的 columns 对不上时，
   render_table 曾静默过滤成 0 列空表，日志还显示执行成功。
2. 默认结果没有兜底：模型没给 viz 就没有报告；纯表格报告也要背 1 MB echarts.js。

运行：
    .venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from pipeline_ai.ai.planner import Planner
from pipeline_ai.core import Context
from pipeline_ai.viz import charts as viz_charts
from pipeline_ai.viz.stage import VizStage

EMPTY_THEAD = '<thead><tr style="background:#f5f5f5;"></tr></thead>'


def web_df() -> pd.DataFrame:
    """模拟 WebSource 现场提取出的中文列名。"""
    return pd.DataFrame(
        {
            "文章标题": ["失物招领启事一", "失物招领启事二", "失物招领启事三"],
            "链接": ["https://a", "https://b", "https://c"],
        }
    )


class ResultFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _run(self, stage: VizStage, df: pd.DataFrame) -> tuple:
        ctx = Context("t")
        stage(df, ctx)
        path = Path(ctx.metadata["charts"])
        return ctx, path.read_text(encoding="utf-8"), path

    # ---------- 运行时列名契约 ----------

    def test_render_table_refuses_silent_empty_table(self) -> None:
        with self.assertRaises(ValueError) as caught:
            viz_charts.render_table(web_df(), columns=["title", "url"])
        self.assertIn("不存在的列", str(caught.exception))
        self.assertIn("文章标题", str(caught.exception), "错误信息应列出可用列")

    def test_stale_plan_columns_fall_back_to_real_columns(self) -> None:
        stage = VizStage(
            output=str(self.dir / "web.html"),
            charts=[{"type": "table", "columns": ["title", "url"]}],
        )
        ctx, html, _ = self._run(stage, web_df())

        self.assertIn("<th>文章标题</th>", html, "应展示运行时真实列名")
        self.assertIn("<th>链接</th>", html)
        self.assertNotIn(EMPTY_THEAD, html, "生成了 0 列空表")
        self.assertEqual(ctx.metadata["viz_kinds"], ["table"])
        self.assertTrue(
            any("回退展示全部" in w for w in ctx.metadata.get("viz_warnings", [])),
            "列名校正应留下告警",
        )

    def test_broken_chart_spec_degrades_to_table(self) -> None:
        stage = VizStage(
            output=str(self.dir / "fallback.html"),
            charts=[{"type": "bar_top", "x": "title", "y": "views"}],
        )
        ctx, html, _ = self._run(stage, web_df())

        self.assertIn("<th>文章标题</th>", html, "图表列猜错时应降级成数据表")
        self.assertNotIn(EMPTY_THEAD, html)
        self.assertEqual(ctx.metadata["viz_kinds"], ["table"])
        self.assertTrue(
            any("降级为数据表" in w for w in ctx.metadata.get("viz_warnings", []))
        )

    def test_existing_columns_are_never_dropped(self) -> None:
        stage = VizStage(
            output=str(self.dir / "ok.html"),
            charts=[{"type": "table", "columns": ["文章标题"]}],
        )
        _, html, _ = self._run(stage, web_df())
        self.assertIn("<th>文章标题</th>", html)
        self.assertNotIn("<th>链接</th>", html)

    # ---------- 默认结果格式 ----------

    def test_pure_table_report_has_header_and_no_echarts(self) -> None:
        stage = VizStage(
            output=str(self.dir / "light.html"),
            charts=[{"type": "table", "title": "数据明细"}],
            title="知乎失物招领",
        )
        _, html, path = self._run(stage, web_df())

        self.assertIn("<h1>知乎失物招领</h1>", html, "报告正文应有标题")
        self.assertIn("共 <b>3</b> 行", html, "摘要头应有行数")
        self.assertIn("field-chip", html, "摘要头应列出字段")
        self.assertNotIn("echarts", html.lower(), "纯表格报告不应内联 echarts.js")
        self.assertLess(path.stat().st_size, 50_000, "纯表格报告应保持轻量")

    def test_chart_report_still_inlines_echarts(self) -> None:
        df = pd.DataFrame({"名称": ["甲", "乙"], "数值": [10, 20]})
        stage = VizStage(
            output=str(self.dir / "chart.html"),
            charts=[{"type": "bar_top", "x": "名称", "y": "数值"}],
        )
        _, html, path = self._run(stage, df)

        self.assertIn("echarts", html.lower(), "有图表时仍需内联 JS")
        self.assertGreater(path.stat().st_size, 100_000)

    def test_planner_adds_default_table_when_no_viz(self) -> None:
        class FakeClient:
            def chat_json(self, messages, **kwargs):
                return {
                    "title": "网页数据",
                    "source": {"type": "web", "params": {"url": "https://example.com"}},
                    "stages": [],
                }

        plan = Planner(client=FakeClient()).plan("随便看看这个网页")
        viz = [s for s in plan["stages"] if s.get("type") == "viz"]

        self.assertEqual(len(viz), 1, "没有可视化时应兜底一个")
        chart = viz[0]["params"]["charts"][0]
        self.assertEqual(chart["type"], "table")
        self.assertNotIn("columns", chart, "动态字段源不应预设列")
        self.assertIn("默认输出数据表", plan.get("reason", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
