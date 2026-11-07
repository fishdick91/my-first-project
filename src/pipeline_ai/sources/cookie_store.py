"""站点 Cookie / User-Agent 存储。

用户在 UI 上粘贴 Cookie 后，存在 cookies/{site}.json。
各 Source 运行时自动从这里加载，不用碰 .env 或代码。

存储格式：
    {
      "site": "douban",
      "cookie": "bid=xxx; dbcl2=yyy; ...",
      "user_agent": "Mozilla/5.0 ...",
      "updated_at": "2026-11-07T12:00:00"
    }
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Dict, List, Optional

# cookies 目录放在项目根下
_COOKIE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "cookies"
)

# 默认 UA，用户没配时用这个
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# 已知站点及其显示名
KNOWN_SITES: Dict[str, str] = {
    "douban": "豆瓣",
    "bilibili": "B站",
}


def _ensure_dir() -> None:
    os.makedirs(_COOKIE_DIR, exist_ok=True)


def _path(site: str) -> str:
    return os.path.join(_COOKIE_DIR, f"{site}.json")


def save(site: str, cookie: str, user_agent: Optional[str] = None) -> Dict[str, str]:
    """保存某个站点的 cookie 和 UA。"""
    _ensure_dir()
    data = {
        "site": site,
        "cookie": cookie.strip(),
        "user_agent": (user_agent or DEFAULT_UA).strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(_path(site), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def load(site: str) -> Optional[Dict[str, str]]:
    """加载某个站点的 cookie 配置，不存在返回 None。"""
    p = _path(site)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def delete(site: str) -> bool:
    """删除某个站点的 cookie，返回是否真的删了。"""
    p = _path(site)
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def list_sites() -> List[Dict[str, str]]:
    """列出所有已配置 cookie 的站点。"""
    _ensure_dir()
    result = []
    for fname in os.listdir(_COOKIE_DIR):
        if not fname.endswith(".json"):
            continue
        site = fname[:-5]
        data = load(site)
        if data:
            result.append(data)
    return result


def is_configured(site: str) -> bool:
    """某个站点是否已配置 cookie。"""
    return load(site) is not None


def build_headers(site: str, base_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """根据站点配置构造请求头。

    - 如果该站点配了 cookie，注入 Cookie 头，UA 也用配置里的
    - 没配就用 base_headers（保留原 UA），不加 Cookie
    """
    headers = dict(base_headers or {})
    data = load(site)
    if data:
        if data.get("cookie"):
            headers["Cookie"] = data["cookie"]
        if data.get("user_agent"):
            headers["User-Agent"] = data["user_agent"]
    return headers
