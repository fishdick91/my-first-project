"""通用网页爬虫：给一个 URL，抓取页面，AI 分析内容提取结构化数据。

抓取策略（HTTP 优先 / 浏览器兜底）与结构化 HTML 清洗放在 scrape_backend.py，
这里保留 Playwright 渲染作为兜底实现。不需要预先配置数据源，输入网址就能爬。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional


def render_page(
    url: str,
    site: Optional[str] = None,
    timeout: int = 30,
    scroll_times: int = 15,
    idle_rounds: int = 2,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    """用 Playwright 打开 URL，返回渲染后的 HTML。

    如果提供了 site，会自动加载该站点的登录态 cookie。
    滚动触发懒加载，但不再无条件滚满 scroll_times 次：每次滚动后比对
    「页面高度 + 候选记录块数量」，连续 idle_rounds 次没有变化就认为到底了。
    scroll_times 变成上限而非固定次数，静态页 / 短列表可提前十几秒结束。
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
                    # 从 URL 提取主域名（去掉 www，保留 .前缀让子域共享）
                    hostname = urlparse(url).hostname or ""
                    parts = hostname.split(".")
                    # 取最后两段作为主域（如 zhihu.com），前面加 .
                    if len(parts) >= 2:
                        domain = "." + ".".join(parts[-2:])
                    else:
                        domain = hostname
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

        # 滚动触发懒加载；页面连续几次不再长高、也不再多出记录块时提前收工。
        probe = """() => ({
            height: document.body ? document.body.scrollHeight : 0,
            count: document.querySelectorAll('li, tr, article, [class*="item"], [class*="card"], [class*="row"]').length
        })"""
        idle_rounds = max(1, idle_rounds)
        scroll_times = max(0, scroll_times)
        signature = None
        idle = 0
        used = 0
        for i in range(scroll_times):
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            try:
                page.wait_for_load_state("networkidle", timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(400)
            used = i + 1
            try:
                state = page.evaluate(probe)
                current = (state.get("height"), state.get("count"))
            except Exception:
                continue  # 探测失败就维持滚动，避免因 JS 异常少抓数据
            if current == signature:
                idle += 1
                if idle >= idle_rounds:
                    break
            else:
                idle = 0
                signature = current

        html = page.content()
        browser.close()
        if log:
            early_stop = used < scroll_times and idle >= idle_rounds
            reason = "页面已稳定，提前结束" if early_stop else "已达滚动上限"
            log(f"🌐 Playwright 滚动 {used}/{scroll_times} 次（{reason}）")
        return html


def simplify_html(html: str, max_length: int = 12000) -> str:
    """精简 HTML：去掉无用标签，保留列表项结构，截断到 max_length。

    保留为兜底实现：没装 Scrapling 时 html_to_llm_text 会回退到这里。
    """
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


def _evaluate_extraction(records: List[Dict[str, Any]]) -> Optional[str]:
    """评估提取结果质量，返回问题描述（None 表示合格）。"""
    if not records:
        return "未提取到任何数据"
    if len(records) < 2:
        return f"只提取到 {len(records)} 条数据，太少了"
    # 收集所有列
    cols: List[str] = []
    seen = set()
    for r in records:
        for k in r.keys():
            if k not in seen:
                cols.append(k)
                seen.add(k)
    if len(cols) < 2:
        return f"只提取到 {len(cols)} 个字段，至少需要2个（名称+值）"
    # 检查每个字段的非空率
    for col in cols:
        non_empty = sum(1 for r in records if r.get(col) not in (None, "", [], {}))
        if non_empty / len(records) < 0.5:
            return f"字段「{col}」空值率超过50%"
    return None


def extract_data_with_ai(html: str, instruction: Optional[str] = None) -> List[Dict[str, Any]]:
    """用 AI 从页面内容提取结构化数据，返回 list of dict。

    带评估闭环：提取后检查质量，不达标则带反馈重提，最多 3 轮。

    Args:
        html: 抓取到的页面 HTML
        instruction: 用户的原始需求描述，用于指导 AI 提取哪些字段
    """
    import os
    from ..ai.llm import LLMClient, extract_json
    from ..prompts import WEB_EXTRACT_PROMPT, WEB_EXTRACT_FEEDBACK_PROMPT
    from .column_names import normalize_extracted_columns
    from .scrape_backend import html_to_llm_text

    # 按 DOM 记录边界清洗：保留链接、去掉噪声标签；未装 Scrapling 时回退正则实现
    simplified = html_to_llm_text(html)
    # 多模型：提取用 LLM_MODEL_EXTRACT，没配就用默认
    extract_model = os.getenv("LLM_MODEL_EXTRACT")
    client = LLMClient(timeout=180, model=extract_model) if extract_model else LLMClient(timeout=180)

    # 用户没给需求时，用一个兜底描述，避免 prompt 里出现空值
    user_instruction = instruction or "提取页面里的主要数据列表的所有可见字段"

    last_feedback: Optional[str] = None
    records: List[Dict[str, Any]] = []
    for _ in range(3):
        if last_feedback:
            user_content = WEB_EXTRACT_FEEDBACK_PROMPT.format(
                feedback=last_feedback, instruction=user_instruction
            ) + simplified
        else:
            user_content = WEB_EXTRACT_PROMPT.format(instruction=user_instruction) + simplified

        messages = [
            {"role": "system", "content": "你是一个网页数据提取助手，只输出 JSON。"},
            {"role": "user", "content": user_content},
        ]
        text = client.chat(messages, temperature=0.0, max_tokens=4096)
        data = extract_json(text)
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict) and "data" in data:
            records = data["data"]
        elif isinstance(data, dict):
            records = [data]
        else:
            records = []

        feedback = _evaluate_extraction(records)
        if feedback is None:
            break  # 质量达标
        last_feedback = feedback

    # 3 轮都不达标也照常返回最后一次结果（有总比没有好），
    # 但列名必须先收敛，否则同一需求每次跑出的表头都不一样。
    return normalize_extracted_columns(records, instruction=instruction)


def scrape_url(
    url: str,
    site: Optional[str] = None,
    instruction: Optional[str] = None,
    strategy: Optional[str] = None,
    log: Optional[Callable[[str], None]] = None,
) -> List[Dict[str, Any]]:
    """完整流程：抓取页面 → 清洗 → AI 提取数据。

    Args:
        url: 要抓取的网页地址
        site: 站点标识（用于加载登录态 cookie）
        instruction: 用户的原始需求描述，传给 AI 指导字段提取
        strategy: auto / fast / browser，见 sources.scrape_backend
        log: 可选日志回调（通常是 ctx.log），记录实际使用的抓取后端
    """
    from .scrape_backend import fetch_html, resolve_strategy

    started = time.monotonic()
    html = fetch_html(url, site=site, strategy=resolve_strategy(strategy, site), log=log)
    fetched = time.monotonic()
    if log:
        log(f"⏱ 抓取阶段 {fetched - started:.1f}s（{len(html)} 字符）")

    records = extract_data_with_ai(html, instruction=instruction)
    if log:
        log(f"⏱ AI 提取阶段 {time.monotonic() - fetched:.1f}s（{len(records)} 行）")
    return records
