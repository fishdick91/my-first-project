"""列名契约回归测试（不调 LLM、不联网）。

锁死的 bug：RenameColumnsStage 曾在 source 之后立刻把列改成中文，
而计划里的 viz.columns 仍是英文，render_table 静默过滤后产出 0 列空表，
日志却显示「rendered 1 visualizations」成功。

运行：
    .venv/Scripts/python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import pandas as pd

from pipeline_ai.ai.executor import build_pipeline
from pipeline_ai.ai.planner import SOURCE_TYPES, validate_plan
from pipeline_ai.core import Context
from pipeline_ai.stages.rename import RenameColumnsStage, resolve_column_aliases, sanitize_mapping
from pipeline_ai.viz.stage import VizStage

ALIASES = {"title": "电影名", "rating": "评分", "director": "导演"}
EN_COLS = ["title", "rating", "director"]
CN_COLS = ["电影名", "评分", "导演"]
EMPTY_THEAD = '<thead><tr style="background:#f5f5f5;"></tr></thead>'


class ColumnAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.csv = self.dir / "movies.csv"
        pd.DataFrame(
            {
                "title": ["肖申克的救赎", "霸王别姬", "阿甘正传"],
                "rating": [9.7, 9.6, 9.5],
                "director": ["弗兰克", "陈凯歌", "罗伯特"],
            }
        ).to_csv(self.csv, index=False, encoding="utf-8")
        self.df = pd.read_csv(self.csv)

    def _plan(self, charts, output: str = "out.html") -> dict:
        return {
            "title": "豆瓣电影",
            "source": {"type": "csv", "params": {"path": str(self.csv)}},
            "stages": [
                {"type": "viz", "params": {"output": str(self.dir / output), "charts": charts}}
            ],
            "instruction": "展示电影名、评分、导演",
        }

    def _read(self, ctx: Context) -> str:
        return Path(ctx.metadata["charts"]).read_text(encoding="utf-8")

    # ---------- 映射清洗 ----------

    def test_sanitize_blocks_invalid_and_colliding_aliases(self) -> None:
        raw = {
            "title": "电影名",      # 合法
            "rating": "评分",      # 合法
            "ghost": "不存在",     # 原列不存在
            "director": "",        # 空名
            "views": "电影名",     # 新名重复
            "actors": "rating",    # 与另一个原始列撞名
        }
        self.assertEqual(
            sanitize_mapping(raw, ["title", "rating", "director", "views", "actors"]),
            {"title": "电影名", "rating": "评分"},
        )
        self.assertEqual(sanitize_mapping("not a dict", EN_COLS), {})

    def test_resolve_column_aliases_builds_prompt_and_cleans_result(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.messages = None

            def chat(self, messages, **kwargs):
                self.messages = messages
                return '{"title": "电影名", "rating": "评分", "ghost": "不存在"}'

        client = FakeClient()
        result = resolve_column_aliases("要电影名、评分", EN_COLS, client=client)

        self.assertEqual(result, {"title": "电影名", "rating": "评分"})
        prompt = client.messages[-1]["content"]
        self.assertIn("要电影名、评分", prompt)
        self.assertIn("director", prompt)
        self.assertEqual(resolve_column_aliases("", EN_COLS, client=client), {})

    # ---------- Stage 行为 ----------

    def test_rename_stage_with_mapping_does_not_call_llm(self) -> None:
        with patch(
            "pipeline_ai.stages.rename.resolve_column_aliases",
            side_effect=AssertionError("不应再调 LLM"),
        ):
            ctx = Context("t")
            result = RenameColumnsStage(mapping=ALIASES)(self.df, ctx)
        self.assertEqual(list(result.columns), CN_COLS)
        self.assertEqual(ctx.metadata["column_aliases"], ALIASES)

    def test_viz_uses_aliases_without_mutating_pipeline_dataframe(self) -> None:
        stage = VizStage(
            output=str(self.dir / "aliases.html"),
            charts=[{"type": "table", "columns": EN_COLS}],
            column_aliases=ALIASES,
        )
        ctx = Context("t")
        returned = stage(self.df, ctx)

        html = self._read(ctx)
        self.assertTrue("<th>电影名</th>" in html, "缺少中文表头 电影名")
        self.assertTrue("<th>评分</th>" in html, "缺少中文表头 评分")
        self.assertTrue(EMPTY_THEAD not in html, "生成了 0 列空表")
        # 关键：不能改管道里的 DataFrame，否则后续环节全错位
        self.assertEqual(list(returned.columns), EN_COLS)

    def test_viz_rewrites_chart_x_and_y(self) -> None:
        stage = VizStage(column_aliases=ALIASES)
        spec = {"type": "bar_top", "x": "title", "y": "rating", "top": 2}
        self.assertEqual(
            stage._apply_aliases(spec),
            {"type": "bar_top", "x": "电影名", "y": "评分", "top": 2},
        )
        # 渲染不应再抛「图表配置引用了不存在的列」
        ctx = Context("t")
        stage = VizStage(
            output=str(self.dir / "bar.html"),
            charts=[spec],
            column_aliases=ALIASES,
        )
        stage(self.df, ctx)
        self.assertTrue(Path(ctx.metadata["charts"]).stat().st_size > 0)

    def test_no_aliases_keeps_original_behaviour(self) -> None:
        ctx = Context("t")
        VizStage(
            output=str(self.dir / "plain.html"),
            charts=[{"type": "table", "columns": EN_COLS}],
        )(self.df, ctx)
        html = self._read(ctx)
        self.assertTrue("<th>title</th>" in html, "缺少原始英文表头 title")
        self.assertTrue(EMPTY_THEAD not in html, "生成了 0 列空表")

    # ---------- 端到端：核心回归 ----------

    def test_build_pipeline_alias_regression(self) -> None:
        plan = self._plan([{"type": "table", "columns": EN_COLS}])
        with patch(
            "pipeline_ai.ai.executor.resolve_column_aliases", return_value=ALIASES
        ):
            ctx = build_pipeline(plan, log_dir=None).run()

        # 图表有中文表头，不是 0 列空表
        html = self._read(ctx)
        self.assertTrue("<th>电影名</th>" in html, "缺少中文表头 电影名")
        self.assertTrue("<th>导演</th>" in html, "缺少中文表头 导演")
        self.assertTrue(EMPTY_THEAD not in html, "生成了 0 列空表")
        # 最终数据（导出/预览用）也是用户字段名
        self.assertEqual(list(ctx.df.columns), CN_COLS)
        # 改名发生在所有环节之后，viz 仍按原始列名工作
        history = "\n".join(ctx.history)
        self.assertLess(history.index("→ viz"), history.index("→ rename_columns"))

    def test_alias_failure_degrades_to_english_columns(self) -> None:
        plan = self._plan([{"type": "table", "columns": EN_COLS}])
        with patch(
            "pipeline_ai.ai.executor.resolve_column_aliases",
            side_effect=RuntimeError("模型不可用"),
        ):
            ctx = build_pipeline(plan, log_dir=None).run()

        html = self._read(ctx)
        self.assertTrue("<th>title</th>" in html, "降级后应保留原始英文表头")
        self.assertTrue(EMPTY_THEAD not in html, "降级后不应出现空表")
        self.assertEqual(list(ctx.df.columns), EN_COLS)
        self.assertTrue(
            any("列名映射解析失败" in line for line in ctx.history),
            "降级原因应写进执行日志",
        )

    def test_web_source_plan_is_valid_and_skips_alias_resolution(self) -> None:
        self.assertIn("web", SOURCE_TYPES)
        plan = {
            "title": "网页",
            "source": {"type": "web", "params": {"url": "https://example.com"}},
            "stages": [],
            "instruction": "提取标题和热度",
        }
        validate_plan(plan, None)
        with patch("pipeline_ai.ai.executor.resolve_column_aliases") as resolver:
            pipeline = build_pipeline(plan, log_dir=None)
        resolver.assert_not_called()
        self.assertEqual(len(pipeline.stages), 1)  # 只有 WebSource，无别名环节


if __name__ == "__main__":
    unittest.main(verbosity=2)
