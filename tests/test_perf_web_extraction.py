"""perf/web-extraction 分支的回归测试（不联网、不调 LLM、不启动浏览器）。

覆盖三件事：
1. 提取列名确定性 —— 同一需求下模型给出不同自由命名，最终列名与顺序必须一致；
2. Planner 对动态字段源代码兜底剥掉 table.columns；
3. Playwright 自适应滚动能提前收工，且分段耗时日志可见。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_ai.ai.llm import LLMError, extract_json
from pipeline_ai.ai.planner import Planner, is_dynamic_fields_source
from pipeline_ai.sources import scrape_backend
from pipeline_ai.sources.column_names import normalize_extracted_columns
from pipeline_ai.sources.web_scraper import extract_data_with_ai, render_page, scrape_url

QUERY = "知乎搜索 失物招领，用表格展示标题和链接"


class ColumnNormalizationTests(unittest.TestCase):
    def test_same_query_always_yields_same_columns(self) -> None:
        """回归：日志里曾出现 文章标题 / 标题 / 名称 三套列名。"""
        variants = [
            [{"文章标题": "a", "网址": "u"}, {"文章标题": "b", "网址": "v"}],
            [{"名称": "a", "链接": "u"}, {"名称": "b", "链接": "v"}],
            [{"链接": "u", "标题": "a"}, {"链接": "v", "标题": "b"}],
        ]
        results = [normalize_extracted_columns(rows, QUERY) for rows in variants]
        for result in results:
            self.assertEqual(list(result[0].keys()), ["标题", "链接"])
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_user_wording_beats_machine_name(self) -> None:
        out = normalize_extracted_columns(
            [{"片名": "x", "分数": "9.7"}, {"片名": "y", "分数": "9.6"}],
            "要电影名、评分、导演",
        )
        self.assertEqual(list(out[0].keys()), ["电影名", "评分"])

    def test_collision_never_drops_data(self) -> None:
        out = normalize_extracted_columns([{"标题": "a", "名称": "b"}], QUERY)
        self.assertEqual(out, [{"标题": "a", "标题_2": "b"}])

    def test_unknown_columns_are_kept(self) -> None:
        out = normalize_extracted_columns([{"标题": "a", "自定义列": "z"}], QUERY)
        self.assertEqual(out, [{"标题": "a", "自定义列": "z"}])

    def test_empty_and_non_dict_input(self) -> None:
        self.assertEqual(normalize_extracted_columns([], QUERY), [])
        raw = ["not", "a", "dict"]
        self.assertEqual(normalize_extracted_columns(raw, QUERY), raw)

    def test_integration_llm_output_is_normalized(self) -> None:
        payload = (
            '[{"文章标题":"甲","网址":"https://a"},'
            '{"文章标题":"乙","网址":"https://b"}]'
        )
        with patch("pipeline_ai.ai.llm.LLMClient") as client_cls:
            client_cls.return_value.chat.return_value = payload
            out = extract_data_with_ai("<html>irrelevant</html>", instruction=QUERY)
        self.assertEqual(
            out,
            [
                {"标题": "甲", "链接": "https://a"},
                {"标题": "乙", "链接": "https://b"},
            ],
        )


class _FakeClient:
    """只实现 Planner 用到的 chat_json。"""

    def __init__(self, plan):
        self.plan = plan

    def chat_json(self, messages, **kwargs):
        return dict(self.plan)


def _plan(source_type: str):
    return {
        "title": "测试",
        "reason": "",
        "source": {"type": source_type, "params": {"url": "https://example.com"}},
        "stages": [
            {
                "type": "viz",
                "params": {
                    "output": "output/result_{date}.html",
                    "title": "测试",
                    "charts": [
                        {"type": "table", "title": "明细", "columns": ["title", "short_link"]}
                    ],
                },
            }
        ],
    }


class DynamicSourcePlanTests(unittest.TestCase):
    def test_dynamic_source_detection(self) -> None:
        self.assertTrue(is_dynamic_fields_source({"type": "web"}))
        self.assertTrue(is_dynamic_fields_source({"type": "zhihu"}))
        self.assertFalse(is_dynamic_fields_source({"type": "douban_top"}))
        self.assertFalse(is_dynamic_fields_source({"type": "bilibili_hot"}))
        self.assertFalse(is_dynamic_fields_source({"type": "csv"}, {"fields": {"a": 1}}))
        self.assertFalse(is_dynamic_fields_source(None))

    def test_strips_guessed_columns_for_web_source(self) -> None:
        plan = Planner(client=_FakeClient(_plan("web"))).plan("整理这个网页的数据")
        charts = plan["stages"][0]["params"]["charts"]
        self.assertNotIn("columns", charts[0], "动态源不该把猜出来的列名写进计划")

    def test_keeps_columns_for_fixed_field_source(self) -> None:
        plan = Planner(client=_FakeClient(_plan("douban_top"))).plan("整理这份数据")
        charts = plan["stages"][0]["params"]["charts"]
        self.assertEqual(charts[0]["columns"], ["title", "short_link"])

    def test_default_table_has_no_columns(self) -> None:
        plan = _plan("web")
        plan["stages"] = []
        result = Planner(client=_FakeClient(plan)).plan("整理这个网页的数据")
        charts = result["stages"][0]["params"]["charts"]
        self.assertEqual(charts[0]["type"], "table")
        self.assertNotIn("columns", charts[0])


class JsonStreamTests(unittest.TestCase):
    """真实验证里模型返回了多段 JSON，原实现抛 Extra data 让整条管道崩掉。"""

    def test_concatenated_arrays_are_merged(self) -> None:
        text = '[{"标题":"甲","链接":"u"}]\n[{"标题":"乙","链接":"v"}]'
        self.assertEqual(
            extract_json(text),
            [{"标题": "甲", "链接": "u"}, {"标题": "乙", "链接": "v"}],
        )

    def test_trailing_prose_with_braces_is_ignored(self) -> None:
        text = '[{"标题":"甲"}] 说明：字段含义见 {} 与 {}'
        self.assertEqual(extract_json(text), [{"标题": "甲"}])

    def test_concatenated_objects_become_a_list(self) -> None:
        self.assertEqual(extract_json('{"a":1}{"a":2}'), [{"a": 1}, {"a": 2}])

    def test_normal_json_and_fenced_json_still_work(self) -> None:
        self.assertEqual(extract_json('{"a": 1,}'), {"a": 1})
        self.assertEqual(extract_json('```json\n[1, 2]\n```'), [1, 2])

    def test_garbage_still_raises(self) -> None:
        with self.assertRaises(LLMError):
            extract_json("这里完全没有 JSON")


class AdaptiveScrollTests(unittest.TestCase):
    @staticmethod
    def _page_with_probe(states):
        """构造一个假的 Playwright page；states 是每轮 probe 的返回值序列。"""
        page = MagicMock()
        page.content.return_value = "<html>ok</html>"
        iterator = iter(states)

        def evaluate(script, *args):
            if script.startswith("() =>"):
                return next(iterator)
            return None

        page.evaluate.side_effect = evaluate
        return page

    def _run_render(self, states, scroll_times=15, log=None):
        page = self._page_with_probe(states)
        with patch("playwright.sync_api.sync_playwright") as sp:
            pw = sp.return_value.__enter__.return_value
            browser = pw.chromium.launch.return_value
            browser.new_context.return_value.new_page.return_value = page
            result = render_page(
                "https://example.com", scroll_times=scroll_times, log=log
            )
        self.assertEqual(result, "<html>ok</html>")
        return page

    def test_stops_early_when_page_stops_growing(self) -> None:
        logs = []
        stable = [{"height": 1000, "count": 10} for _ in range(15)]
        page = self._run_render(stable, log=logs.append)
        # 3 轮后连续 2 次无变化即停；每轮 evaluate 调 2 次（滚动 + probe）
        self.assertEqual(page.wait_for_timeout.call_count, 3)
        page.wait_for_timeout.assert_called_with(400)
        self.assertTrue(any("提前结束" in line for line in logs))
        self.assertTrue(any("滚动 3/15" in line for line in logs))

    def test_keeps_scrolling_while_content_grows(self) -> None:
        logs = []
        growing = [{"height": 1000 + i * 100, "count": 10 + i} for i in range(15)]
        page = self._run_render(growing, scroll_times=5, log=logs.append)
        self.assertEqual(page.wait_for_timeout.call_count, 5)
        self.assertTrue(any("已达滚动上限" in line for line in logs))

    def test_zero_scrolls_is_allowed(self) -> None:
        logs = []
        page = self._run_render([], scroll_times=0, log=logs.append)
        self.assertEqual(page.wait_for_timeout.call_count, 0)


class TimingLogTests(unittest.TestCase):
    def test_scrape_url_logs_both_stages(self) -> None:
        logs = []
        with patch.object(scrape_backend, "fetch_html", return_value="<html>x</html>"), patch(
            "pipeline_ai.sources.web_scraper.extract_data_with_ai",
            return_value=[{"标题": "a"}],
        ):
            scrape_url("https://example.com", strategy="fast", log=logs.append)
        self.assertTrue(any("⏱ 抓取阶段" in line for line in logs))
        self.assertTrue(any("⏱ AI 提取阶段" in line for line in logs))

    def test_browser_fallback_logs_render_duration(self) -> None:
        logs = []
        with patch.object(scrape_backend, "http_get", return_value=None), patch(
            "pipeline_ai.sources.web_scraper.render_page", return_value="<html>x</html>"
        ):
            fetch_html = scrape_backend.fetch_html
            fetch_html("https://example.com", strategy="auto", log=logs.append)
        self.assertTrue(any("⏱ Playwright 渲染耗时" in line for line in logs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
