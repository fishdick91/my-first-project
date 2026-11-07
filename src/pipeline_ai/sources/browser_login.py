"""零知识登录：弹浏览器让用户正常登录，自动捕获 Cookie。

用户完全不需要知道 Cookie 是什么，流程：
    1. 程序用 Playwright 打开目标网站（有头浏览器）
    2. 用户像平时一样扫码 / 输账号密码登录
    3. 程序检测到登录态 cookie 出现，自动保存到 cookie_store
    4. 关闭浏览器

各站点的登录态标识 cookie：
    douban   -> dbcl2
    bilibili -> SESSDATA
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from . import cookie_store

# 各站点登录后的首页 URL
LOGIN_URLS = {
    "douban": "https://www.douban.com/",
    "bilibili": "https://www.bilibili.com/",
}

# 各站点的登录态标识 cookie（出现这个说明登录成功）
LOGIN_MARKERS = {
    "douban": "dbcl2",
    "bilibili": "SESSDATA",
}

# 等待用户登录的最长时间（秒）
LOGIN_TIMEOUT = 300


def cookies_to_string(cookies: list) -> str:
    """把 Playwright 的 cookie 列表转成 requests 能用的 Cookie 头字符串。"""
    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)


def run_login(site: str, timeout: int = LOGIN_TIMEOUT) -> bool:
    """启动浏览器让用户登录，成功后保存 cookie。

    返回 True 表示登录成功并已保存，False 表示超时或失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[登录] 未安装 playwright，无法自动登录", file=sys.stderr)
        return False

    if site not in LOGIN_URLS:
        print(f"[登录] 不支持的站点: {site}", file=sys.stderr)
        return False

    marker = LOGIN_MARKERS[site]
    url = LOGIN_URLS[site]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"[登录] 已打开 {site} 登录页，请在浏览器里完成登录…")
        page.goto(url)

        # 轮询检测登录态 cookie
        start = time.time()
        logged_in = False
        while time.time() - start < timeout:
            cookies = context.cookies()
            cookie_names = {c["name"] for c in cookies}
            if marker in cookie_names:
                logged_in = True
                break
            time.sleep(2)

        if not logged_in:
            print(f"[登录] 等待超时（{timeout}s），未检测到登录态", file=sys.stderr)
            browser.close()
            return False

        # 拿到 UA（登录用的什么 UA，爬取就用什么 UA，避免风控）
        ua = page.evaluate("navigator.userAgent")

        # 保存
        cookie_str = cookies_to_string(cookies)
        cookie_store.save(site, cookie_str, user_agent=ua)
        print(f"[登录] ✅ 已保存 {site} 的登录态（{len(cookies)} 个 cookie）")

        # 给用户 2 秒看到登录成功的页面
        time.sleep(2)
        browser.close()
        return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m pipeline_ai.sources.browser_login <site>")
        print(f"可选站点: {', '.join(LOGIN_URLS)}")
        sys.exit(1)
    site = sys.argv[1]
    ok = run_login(site)
    sys.exit(0 if ok else 1)
