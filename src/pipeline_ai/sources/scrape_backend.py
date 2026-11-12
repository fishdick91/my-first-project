"""网页抓取后端：把 Scrapling 的分层抓取思路接到现有链路上。

参考 https://github.com/D4Vinci/Scrapling 的两点：

1. Fetcher / StealthyFetcher / DynamicFetcher 按成本递增、失败逐级降级。
   本项目对应 fast(HTTP) -> browser(Playwright)，用「内容体检」判断是否需要升级：
   静态页面一次 HTTP 就够；SPA 壳 / 403 / 懒加载页面自动降级到浏览器。
2. 用解析器的 DOM 定位重复记录块，而不是用正则在整页 HTML 上硬切。
   输出给 LLM 的是「每行一条记录」的精简 HTML 片段，保留 <a href> 链接。

Scrapling 是**可选依赖**：基础包只含 parser，fetchers 需要额外 extras。
任何能力缺失都会自动回退，没装 scrapling 时行为与旧版一致。

策略（优先级：显式参数 > 数据源 scrape_strategy > 环境变量 SCRAPE_STRATEGY > auto）：
    auto    先快速 HTTP，内容不足再降级 Playwright（默认）
    fast    只用 HTTP，失败直接报错
    browser 只用 Playwright（旧行为，一键回退）
"""

from __future__ import annotations

import os
import re
from typing import Callable, List, Optional, Sequence

# 这些标签对「抽取数据列表」没有价值，喂给 LLM 只会浪费 token
IGNORE_TAGS = (
    "script", "style", "noscript", "svg", "nav", "header",
    "footer", "form", "iframe", "button", "head", "link", "meta", "base",
)

# 常见的「重复记录」容器，按优先顺序尝试
REPEAT_SELECTORS = (
    "li",
    "tr",
    "article",
    "div[class*='item']",
    "div[class*='card']",
    "div[class*='row']",
    "div[class*='list']",
)

STRATEGIES = {"auto", "fast", "browser"}


def _selector(html: str):
    """构造 Scrapling Selector；未安装或解析失败时返回 None。"""
    try:
        from scrapling.parser import Selector

        return Selector(content=html)
    except Exception:
        return None


def _repeated_blocks(page, min_items: int = 3) -> List:
    """找出最像「数据列表」的一组重复元素。

    先用常见选择器，再按 (tag, class) 自动分组找相似元素 —— 借鉴 Scrapling
    的 find_similar 思路，不依赖网站把 class 恰好命名成 item/card/row。
    """
    best: List = []
    for selector in REPEAT_SELECTORS:
        try:
            nodes = list(page.css(selector))
        except Exception:
            continue
        if len(nodes) > len(best):
            best = nodes

    try:
        groups = {}
        for node in list(page.css("li, tr, article, section, div[class]"))[:500]:
            text = str(
                node.get_all_text(separator=" ", strip=True, ignore_tags=IGNORE_TAGS)
            )
            if not 10 <= len(text) <= 2000:  # 跳过空壳和整页容器
                continue
            classes = " ".join(sorted(str(node.attrib.get("class", "")).split()))
            groups.setdefault((str(node.tag), classes), []).append(node)
        for nodes in groups.values():
            if len(nodes) > len(best):
                best = nodes
    except Exception:
        pass

    return best if len(best) >= min_items else []


def _clean_fragment(fragment: str) -> str:
    """清掉噪声标签，压缩空白，但保留 <a href> 等有信息量的属性。"""
    for tag in IGNORE_TAGS:
        fragment = re.sub(
            rf"<{tag}\b[^>]*>[\s\S]*?</{tag}>", " ", fragment, flags=re.IGNORECASE
        )
        fragment = re.sub(rf"<{tag}\b[^>]*/?>", " ", fragment, flags=re.IGNORECASE)
    fragment = re.sub(r"<!--[\s\S]*?-->", " ", fragment)
    return re.sub(r"\s+", " ", fragment).strip()


def html_to_llm_text(html: str, max_length: int = 12000) -> str:
    """把整页 HTML 变成「每行一条候选记录」的紧凑文本，供 AI 提取。

    与旧的 simplify_html 相比：按 DOM 边界切分（不会把 <li> 切一半）、
    去噪更彻底、保留链接。Scrapling 不可用时回退到原来的正则实现。
    """
    page = _selector(html)
    if page is None:
        from .web_scraper import simplify_html

        return simplify_html(html, max_length)

    blocks = _repeated_blocks(page)
    if blocks:
        lines = []
        for node in blocks[:200]:
            fragment = _clean_fragment(str(node.html_content))
            if fragment:
                lines.append(fragment)
    else:
        whole = _clean_fragment(str(page.html_content))
        lines = whole.split("\n") if whole else []

    # 按记录边界截断，绝不切断半条记录
    kept: List[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > max_length:
            break
        kept.append(line)
        used += len(line) + 1
    if kept:
        return "\n".join(kept)
    return _clean_fragment(str(page.html_content))[:max_length]


def content_is_sufficient(html: str, min_items: int = 3, min_chars: int = 200) -> bool:
    """判断快速 HTTP 拿到的 HTML 是否够 AI 提取，不够就该升级到浏览器。

    典型反例：SPA 只返回一个 <div id="root"></div>，文本极短、无重复结构。
    """
    if not html or len(html) < min_chars:
        return False

    page = _selector(html)
    if page is None:
        # 无解析器时靠标签计数判断；HTML 长度已在上面校验过
        text = re.sub(r"<[^>]+>", " ", html).strip()
        listed = len(re.findall(r"<li\b|<tr\b|<article\b", html, flags=re.IGNORECASE))
        return listed >= min_items and len(text) >= 20

    blocks = _repeated_blocks(page, min_items=1)
    text = str(
        page.get_all_text(separator=" ", strip=True, ignore_tags=IGNORE_TAGS)
    )
    return len(blocks) >= min_items or (len(text) >= min_chars and bool(blocks))


def _headers(site: Optional[str]) -> dict:
    from . import cookie_store

    base = {
        "User-Agent": cookie_store.DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    return cookie_store.build_headers(site, base) if site else base


def http_get(url: str, site: Optional[str] = None, timeout: int = 15) -> Optional[str]:
    """快速 HTTP 抓取。优先用 Scrapling Fetcher（TLS 指纹），否则用 requests。

    任何异常都返回 None，由调用方决定降级；不会让主链路崩掉。
    """
    headers = _headers(site)

    try:
        from scrapling.fetchers import Fetcher

        page = Fetcher.get(url, headers=headers, timeout=timeout, impersonate="chrome")
        body = getattr(page, "body", b"")
        encoding = getattr(page, "encoding", None) or "utf-8"
        html = body.decode(encoding, errors="replace") if isinstance(body, bytes) else str(body or "")
        if not html:
            html = str(getattr(page, "html_content", "") or "")
        if html:
            return html
    except Exception:
        pass  # fetchers 未安装或调用失败，回退到项目已有的 requests

    try:
        import requests

        response = requests.get(url, headers=headers, timeout=timeout)
        if response.status_code >= 400:
            return None
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = response.apparent_encoding or "utf-8"
        return response.text or None
    except Exception:
        return None


def resolve_strategy(explicit: Optional[str] = None, site: Optional[str] = None) -> str:
    """解析抓取策略：显式参数 > 数据源配置 > 环境变量 > 默认 auto。"""
    raw = explicit
    if not raw and site:
        from . import registry

        cfg = registry.get_source_config(site) or {}
        raw = cfg.get("scrape_strategy")
    if not raw:
        raw = os.getenv("SCRAPE_STRATEGY", "auto")
    value = str(raw).strip().lower()
    return value if value in STRATEGIES else "auto"


def fetch_html(
    url: str,
    site: Optional[str] = None,
    timeout: int = 30,
    strategy: str = "auto",
    scroll_times: int = 15,
    log: Optional[Callable[[str], None]] = None,
) -> str:
    """按策略取回 HTML，返回渲染后的 HTML 字符串。"""
    from .web_scraper import render_page  # 延迟导入，避免与 web_scraper 循环依赖

    def emit(message: str) -> None:
        if log:
            log(message)

    strategy = strategy if strategy in STRATEGIES else "auto"

    if strategy != "browser":
        html = http_get(url, site=site, timeout=min(timeout, 15))
        if html and content_is_sufficient(html):
            emit(f"⚡ 快速 HTTP 抓取命中（{len(html)} 字符），跳过浏览器渲染")
            return html
        if strategy == "fast":
            raise RuntimeError(
                "快速 HTTP 抓取失败或内容不足；SCRAPE_STRATEGY=fast 不会降级，"
                "可改用 auto 或 browser"
            )
        emit("⚡ 快速 HTTP 内容不足，降级 Playwright 渲染")

    return render_page(url, site=site, timeout=timeout, scroll_times=scroll_times)
