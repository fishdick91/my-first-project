"""B 站热门视频数据源（同步版）。

接口：https://api.bilibili.com/x/web-interface/popular?ps=N&pn=1
返回 JSON，data.list 是视频列表。

解析逻辑抽在 parse_videos() 里，异步版（async_bilibili_hot）复用同一份。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import Source, request_with_retry
from ..core.context import Context


def parse_videos(videos: List[Dict[str, Any]]) -> pd.DataFrame:
    """把 B 站接口返回的 video 列表转成 DataFrame。"""
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
    return pd.DataFrame(rows)


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

    def __init__(self, limit: int = 20, timeout: int = 10, retries: int = 3) -> None:
        self.limit = limit
        self.timeout = timeout
        self.retries = retries

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        last_error: Optional[Exception] = None

        # B 站对热门接口有频率限制，短时间内频繁请求会返回
        # code != 0 的"请求错误"。这里把业务错误也纳入重试。
        for attempt in range(1, self.retries + 1):
            try:
                resp = request_with_retry(
                    self.API_URL,
                    params={"ps": self.limit, "pn": 1},
                    headers=self.HEADERS,
                    timeout=self.timeout,
                    retries=1,
                )
                payload = resp.json()

                if payload.get("code") != 0:
                    raise RuntimeError(
                        f"Bilibili API error: {payload.get('message')} "
                        f"(code={payload.get('code')})"
                    )

                result = parse_videos(payload["data"]["list"])
                ctx.set_source(f"bilibili_hot:{self.limit}", len(result))
                return result

            except RuntimeError as exc:
                last_error = exc
                if attempt < self.retries:
                    ctx.log(f"bilibili_hot 第 {attempt} 次失败，等待重试: {exc}")
                    time.sleep(2 * attempt)

        raise RuntimeError(
            f"抓取 B 站热门视频失败（已重试 {self.retries} 次）: {last_error}。"
            "可能是接口临时限流，等几十秒再试即可。"
        ) from last_error
