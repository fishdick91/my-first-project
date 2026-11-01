"""B 站热门视频数据源。

接口：https://api.bilibili.com/x/web-interface/popular?ps=N&pn=1
返回 JSON，data.list 是视频列表。
"""

from __future__ import annotations

import time
from typing import Optional

import pandas as pd
import requests

from .base import Source
from ..core.context import Context


class BilibiliHotSource(Source):
    name = "bilibili_hot"

    API_URL = "https://api.bilibili.com/x/web-interface/popular"

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }

    def __init__(self, limit: int = 20) -> None:
        self.limit = limit

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        params = {"ps": self.limit, "pn": 1}
        resp = requests.get(self.API_URL, params=params, headers=self.HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()

        if payload.get("code") != 0:
            raise RuntimeError(f"Bilibili API error: {payload.get('message')}")

        videos = payload["data"]["list"]
        rows = []
        for v in videos:
            stat = v.get("stat", {})
            rows.append({
                "bvid": v.get("bvid"),
                "title": v.get("title"),
                "author": (v.get("owner") or {}).get("name"),
                "views": stat.get("view", 0),
                "danmaku": stat.get("danmaku", 0),
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "favorites": stat.get("favorite", 0),
                "shares": stat.get("share", 0),
                "duration": v.get("duration", 0),
                "pubdate": pd.to_datetime(v.get("pubdate", 0), unit="s"),
                "short_link": v.get("short_link_v2"),
            })

        result = pd.DataFrame(rows)
        ctx.set_source(f"bilibili_hot:{self.limit}", len(result))
        return result
