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


def run_login(site: str, url: Optional[str] = None, timeout: int = LOGIN_TIMEOUT) -> bool:
    """启动浏览器让用户登录，成功后保存 cookie。

    - 已知站点（douban/bilibili）：用预设 URL，检测登录态 marker
    - 未知站点：用传入的 url，不检测 marker，等待超时后保存所有 cookie

    返回 True 表示已保存 cookie，False 表示失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[登录] 未安装 playwright，无法自动登录", file=sys.stderr)
        return False

    # 已知站点用预设配置
    if site in LOGIN_URLS:
        marker = LOGIN_MARKERS[site]
        login_url = LOGIN_URLS[site]
    else:
        # 未知站点：用传入的 url，不检测特定 marker
        if not url:
            print(f"[登录] 未知站点 {site} 必须提供 url", file=sys.stderr)
            return False
        marker = None
        login_url = url

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        print(f"[登录] 已打开 {site}，请在浏览器里完成登录…")
        page.goto(login_url)

        # 轮询检测登录态
        start = time.time()
        logged_in = False
        while time.time() - start < timeout:
            cookies = context.cookies()
            if marker:
                cookie_names = {c["name"] for c in cookies}
                if marker in cookie_names:
                    logged_in = True
                    break
            else:
                # 未知站点：cookie 数量 > 5 就认为有登录态了（简单判断）
                if len(cookies) > 5:
                    logged_in = True
                    break
            time.sleep(2)

        if not logged_in:
            print(f"[登录] 等待超时（{timeout}s），仍保存当前 cookie", file=sys.stderr)

        # 拿到 UA
        try:
            ua = page.evaluate("navigator.userAgent")
        except Exception:
            ua = None

        # 保存所有 cookie
        cookies = context.cookies()
        cookie_str = cookies_to_string(cookies)
        cookie_store.save(site, cookie_str, user_agent=ua)
        print(f"[登录] ✅ 已保存 {site} 的登录态（{len(cookies)} 个 cookie）")

        time.sleep(2)
        browser.close()
        return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python -m pipeline_ai.sources.browser_login <site> [url]")
        print(f"已知站点: {', '.join(LOGIN_URLS)}")
        sys.exit(1)
    site = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else None
    ok = run_login(site, url=url)
    sys.exit(0 if ok else 1)
