"""通用网页爬虫：给一个 URL，用 Playwright 渲染，AI 分析 HTML 提取结构化数据。

不需要预先配置数据源，输入网址就能爬。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..ai.llm import LLMClient, extract_json


def render_page(url: str, site: Optional[str] = None, timeout: int = 30, scroll_times: int = 5) -> str:
    """用 Playwright 打开 URL，返回渲染后的 HTML。

    如果提供了 site，会自动加载该站点的登录态 cookie。
    会自动滚动页面触发懒加载。
    """
    from playwright.sync_api import sync_playwright
    from . import cookie_store
    from urllib.parse import urlparse

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        # 加载登录态
        if site:
            stored = cookie_store.load(site)
            if stored:
                cookie_str = stored.get("cookie", "")
                if cookie_str:
                    # 从 URL 提取域名
                    domain = urlparse(url).hostname
                    # 把 cookie 字符串转成 Playwright 格式
                    cookies = []
                    for pair in cookie_str.split(";"):
                        pair = pair.strip()
                        if "=" in pair:
                            name, value = pair.split("=", 1)
                            cookies.append({
                                "name": name.strip(),
                                "value": value.strip(),
                                "domain": domain,
                                "path": "/",
                            })
                    if cookies:
                        context.add_cookies(cookies)
                ua = stored.get("user_agent")
                if ua:
                    context.set_extra_http_headers({"User-Agent": ua})

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        # 等待网络空闲
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        # 滚动触发懒加载
        for _ in range(scroll_times):
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            page.wait_for_timeout(500)

        html = page.content()
        browser.close()
        return html


def simplify_html(html: str, max_length: int = 20000) -> str:
    """精简 HTML：去掉无用标签，保留列表项结构，截断到 max_length。"""
    # 去掉 script、style、noscript
    html = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<style[\s\S]*?</style>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<noscript[\s\S]*?</noscript>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<svg[\s\S]*?</svg>", "", html, flags=re.IGNORECASE)
    # 去掉注释
    html = re.sub(r"<!--[\s\S]*?-->", "", html)
    # 去掉头部和导航
    html = re.sub(r"<head[\s\S]*?</head>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<header[\s\S]*?</header>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<nav[\s\S]*?</nav>", "", html, flags=re.IGNORECASE)
    html = re.sub(r"<footer[\s\S]*?</footer>", "", html, flags=re.IGNORECASE)
    # 去掉多余空白
    html = re.sub(r"\s+", " ", html)

    if len(html) <= max_length:
        return html

    # 超长时：优先提取列表项和表格行
    li_items = re.findall(r"<li[\s\S]*?</li>", html, flags=re.IGNORECASE)
    tr_items = re.findall(r"<tr[\s\S]*?</tr>", html, flags=re.IGNORECASE)
    div_items = re.findall(r'<div[^>]*class="[^"]*(?:item|card|rank|row|list)[^"]*"[\s\S]*?</div>', html, flags=re.IGNORECASE)

    priority = " ".join(li_items + tr_items + div_items)
    if len(priority) > max_length:
        return priority[:max_length]
    # 剩余空间补其他内容
    remaining = max_length - len(priority)
    text_only = re.sub(r"<[^>]+>", " ", html)
    return priority + " " + text_only[-remaining:] if remaining > 0 else priority


_AI_EXTRACT_PROMPT = """你是一个网页数据提取助手。我会给你一段网页 HTML，你要从中提取结构化数据。

规则：
1. 识别页面里的主要数据列表（热搜榜、排行榜、文章列表等）
2. 提取每条记录的所有可见字段
3. 只输出 JSON 数组，不要解释，不要 markdown
4. 字段名用页面里的原始文字（如"标题"、"热度"、"排名"）
5. 如果有排名，从1开始编号
6. 最多提取 50 条记录

输出格式：
[{"标题": "...", "热度": "...", "链接": "..."}]

HTML 内容：
"""


def extract_data_with_ai(html: str) -> List[Dict[str, Any]]:
    """用 AI 从 HTML 提取结构化数据，返回 list of dict。"""
    simplified = simplify_html(html)
    client = LLMClient()
    messages = [
        {"role": "system", "content": "你是一个网页数据提取助手，只输出 JSON。"},
        {"role": "user", "content": _AI_EXTRACT_PROMPT + simplified},
    ]
    text = client.chat(messages, temperature=0.0, max_tokens=4096)
    data = extract_json(text)
    if isinstance(data, list):
        return data
    # 如果返回的是 dict 包一层
    if isinstance(data, dict) and "data" in data:
        return data["data"]
    return [data] if isinstance(data, dict) else []


def scrape_url(url: str, site: Optional[str] = None) -> List[Dict[str, Any]]:
    """完整流程：渲染页面 → AI 提取数据。"""
    html = render_page(url, site=site)
    return extract_data_with_ai(html)
