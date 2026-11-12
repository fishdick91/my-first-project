"""Scrapling 优化分支的回归测试（不联网、不调 LLM、不启动浏览器）。

覆盖：结构化清洗、内容体检、HTTP 优先 / 浏览器兜底的策略切换、策略优先级、
以及全链路参数透传。所有网络与渲染调用都用 mock 替换。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pipeline_ai.core import Context
from pipeline_ai.prompts import WEB_EXTRACT_FEEDBACK_PROMPT, WEB_EXTRACT_PROMPT
from pipeline_ai.sources import scrape_backend
from pipeline_ai.sources.scrape_backend import (
    content_is_sufficient,
    fetch_html,
    html_to_llm_text,
    resolve_strategy,
)
from pipeline_ai.sources.web_scraper import scrape_url
from pipeline_ai.sources.web_source import WebSource

LIST_PAGE = (
    "<html><body><nav>导航噪声</nav><ul>"
    + "".join(
        f'<li><a href="https://example.com/{i}">标题{i}</a><span>热度{i * 100}</span></li>'
        for i in range(6)
    )
    + "<script>alert('noise')</script></body></html>"
)
SPA_SHELL = '<html><body><div id="root"></div><script src="app.js"></script></body></html>'


class HtmlCleaningTests(unittest.TestCase):
    def test_structured_cleaning_keeps_links_and_drops_noise(self) -> None:
        out = html_to_llm_text(LIST_PAGE)
        self.assertEqual(len(out.splitlines()), 6, "每条记录应独占一行")
        self.assertIn('https://example.com/0', out, "必须保留 href，否则链接字段会丢")
        self.assertNotIn("alert", out)
        self.assertNotIn("导航噪声", out)

    def test_truncation_never_cuts_a_record_in_half(self) -> None:
        out = html_to_llm_text(LIST_PAGE, max_length=120)
        for line in out.splitlines():
            self.assertTrue(line.startswith("<li>") and line.endswith("</li>"))

    def test_falls_back_to_regex_without_scrapling(self) -> None:
        with patch.object(scrape_backend, "_selector", return_value=None):
            out = html_to_llm_text(LIST_PAGE)
        self.assertIn("https://example.com/0", out, "回退实现也要保留链接")
        self.assertNotIn("alert", out)

    def test_prompt_templates_still_format(self) -> None:
        self.assertIn("网页内容", WEB_EXTRACT_PROMPT.format(instruction="要标题"))
        self.assertIn("链接", WEB_EXTRACT_FEEDBACK_PROMPT.format(feedback="少了", instruction="要链接"))


class ContentCheckTests(unittest.TestCase):
    def test_list_page_is_sufficient(self) -> None:
        self.assertTrue(content_is_sufficient(LIST_PAGE))

    def test_spa_shell_is_not_sufficient(self) -> None:
        self.assertFalse(content_is_sufficient(SPA_SHELL))
        self.assertFalse(content_is_sufficient(""))

    def test_without_scrapling_uses_regex_fallback(self) -> None:
        with patch.object(scrape_backend, "_selector", return_value=None):
            self.assertTrue(content_is_sufficient(LIST_PAGE))
            self.assertFalse(content_is_sufficient(SPA_SHELL))


class StrategyTests(unittest.TestCase):
    def test_auto_uses_http_when_content_is_enough(self) -> None:
        logs = []
        with patch.object(scrape_backend, "http_get", return_value=LIST_PAGE) as http, patch(
            "pipeline_ai.sources.web_scraper.render_page"
        ) as browser:
            html = fetch_html("https://example.com", strategy="auto", log=logs.append)
        self.assertEqual(html, LIST_PAGE)
        http.assert_called_once()
        browser.assert_not_called()
        self.assertTrue(any("跳过浏览器渲染" in line for line in logs))

    def test_auto_upgrades_to_browser_when_http_is_insufficient(self) -> None:
        logs = []
        with patch.object(scrape_backend, "http_get", return_value=SPA_SHELL), patch(
            "pipeline_ai.sources.web_scraper.render_page", return_value=LIST_PAGE
        ) as browser:
            html = fetch_html("https://example.com", strategy="auto", log=logs.append)
        self.assertEqual(html, LIST_PAGE)
        browser.assert_called_once()
        self.assertTrue(any("降级 Playwright" in line for line in logs))

    def test_auto_upgrades_when_http_fails(self) -> None:
        with patch.object(scrape_backend, "http_get", return_value=None), patch(
            "pipeline_ai.sources.web_scraper.render_page", return_value=LIST_PAGE
        ) as browser:
            fetch_html("https://example.com", strategy="auto")
        browser.assert_called_once()

    def test_fast_fails_loudly_instead_of_silently_rendering(self) -> None:
        with patch.object(scrape_backend, "http_get", return_value=None), patch(
            "pipeline_ai.sources.web_scraper.render_page"
        ) as browser:
            with self.assertRaises(RuntimeError):
                fetch_html("https://example.com", strategy="fast")
        browser.assert_not_called()

    def test_browser_keeps_legacy_behaviour(self) -> None:
        with patch.object(scrape_backend, "http_get") as http, patch(
            "pipeline_ai.sources.web_scraper.render_page", return_value=LIST_PAGE
        ) as browser:
            html = fetch_html("https://example.com", strategy="browser")
        self.assertEqual(html, LIST_PAGE)
        http.assert_not_called()
        browser.assert_called_once()

    def test_strategy_precedence(self) -> None:
        with patch.dict(os.environ, {"SCRAPE_STRATEGY": "fast"}), patch(
            "pipeline_ai.sources.registry.get_source_config",
            return_value={"scrape_strategy": "browser"},
        ):
            self.assertEqual(resolve_strategy("auto", "zhihu"), "auto")
            self.assertEqual(resolve_strategy(None, "zhihu"), "browser")
            self.assertEqual(resolve_strategy(), "fast")
        self.assertEqual(resolve_strategy("not-a-strategy"), "auto")


class WiringTests(unittest.TestCase):
    def test_scrape_url_passes_strategy_and_log(self) -> None:
        logs = []
        with patch.object(scrape_backend, "fetch_html", return_value=LIST_PAGE) as fetch, patch(
            "pipeline_ai.sources.web_scraper.extract_data_with_ai", return_value=[{"标题": "x"}]
        ):
            records = scrape_url(
                "https://example.com", site="zhihu", instruction="要标题",
                strategy="fast", log=logs.append,
            )
        self.assertEqual(records, [{"标题": "x"}])
        self.assertEqual(fetch.call_args.kwargs["strategy"], "fast")
        self.assertIsNotNone(fetch.call_args.kwargs["log"])

    def test_web_source_forwards_strategy_and_ctx_log(self) -> None:
        ctx = Context("t")
        with patch(
            "pipeline_ai.sources.web_source.scrape_url",
            return_value=[{"标题": "a"}, {"标题": "b"}],
        ) as mocked:
            source = WebSource(url="https://example.com", site="zhihu", strategy="fast", instruction="要标题")
            df = source(None, ctx)
        self.assertEqual(len(df), 2)
        self.assertEqual(mocked.call_args.kwargs["strategy"], "fast")
        log = mocked.call_args.kwargs["log"]
        log("probe from scraper")
        self.assertTrue(any("正在抓取网页" in line for line in ctx.history))
        self.assertTrue(any("probe from scraper" in line for line in ctx.history))


if __name__ == "__main__":
    unittest.main(verbosity=2)
