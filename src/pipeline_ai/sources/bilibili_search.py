"""B 站关键词搜索数据源。

接口：https://api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=xxx
和热门接口（bilibili_hot）的区别：
- 热门接口不要 cookie 就能用；搜索接口必须带 buvid3 设备指纹 cookie，
  否则会被 B 站的反爬挡掉，返回 412。
- 搜索结果的 title 里会有 <em class="keyword"> 高亮标签，要剥掉。
- 搜索结果没有 coins / shares 字段，统一补 0。
"""

from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import Source, request_with_retry
from . import cookie_store
from ..core.context import Context


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(text: str) -> str:
    """去掉 title 里的 <em class=\"keyword\"> 等 HTML 标签。"""
    return _TAG_RE.sub("", text) if text else text


def parse_search_results(videos: List[Dict[str, Any]]) -> pd.DataFrame:
    """把搜索接口返回的 video 列表转成 DataFrame。"""
    rows = []
    for v in videos:
        rows.append({
            "bvid": v.get("bvid"),
            "title": _strip_tags(v.get("title", "")),
            "author": v.get("author"),
            "views": v.get("play", 0),
            "danmaku": v.get("danmaku", v.get("video_review", 0)),
            "likes": v.get("like", 0),
            "coins": 0,  # 搜索结果没有这个字段
            "favorites": v.get("favorites", 0),
            "shares": 0,  # 搜索结果没有这个字段
            "duration": v.get("duration", 0),
            "pubdate": pd.to_datetime(v.get("pubdate", 0), unit="s"),
            "short_link": v.get("arcurl"),
        })
    return pd.DataFrame(rows)


class BilibiliSearchSource(Source):
    name = "bilibili_search"

    API_URL = "https://api.bilibili.com/x/web-interface/search/type"

    # 搜索接口必须带 buvid3，否则被反爬挡掉。这里每次启动随机生成一个。
    DEFAULT_HEADERS = {
        "User-Agent": cookie_store.DEFAULT_UA,
        "Referer": "https://www.bilibili.com/",
    }

    def __init__(
        self,
        keyword: str,
        limit: int = 20,
        timeout: int = 10,
        retries: int = 3,
    ) -> None:
        self.keyword = keyword
        self.limit = limit
        self.timeout = timeout
        self.retries = retries
        self._buvid = uuid.uuid4().hex

    @property
    def headers(self) -> Dict[str, str]:
        # 先从 cookie_store 加载登录态（有就用登录 cookie，没有就裸抓）
        h = cookie_store.build_headers("bilibili", self.DEFAULT_HEADERS)
        # 搜索接口必须带 buvid3；如果登录 cookie 里没带，就补一个随机的
        cookie = h.get("Cookie", "")
        if "buvid3" not in cookie:
            buvid = f"buvid3={self._buvid}; buvid4={uuid.uuid4().hex}-{uuid.uuid4().hex}"
            h["Cookie"] = f"{cookie}; {buvid}" if cookie else buvid
        return h

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retries + 1):
            try:
                resp = request_with_retry(
                    self.API_URL,
                    params={
                        "search_type": "video",
                        "keyword": self.keyword,
                        "page": 1,
                    },
                    headers=self.headers,
                    timeout=self.timeout,
                    retries=1,
                )
                payload = resp.json()

                if payload.get("code") != 0:
                    raise RuntimeError(
                        f"Bilibili search API error: {payload.get('message')} "
                        f"(code={payload.get('code')})"
                    )

                result = parse_search_results(payload["data"]["result"] or [])
                if self.limit and len(result) > self.limit:
                    result = result.head(self.limit)
                ctx.set_source(f"bilibili_search:{self.keyword}", len(result))
                return result

            except (RuntimeError, KeyError) as exc:
                last_error = exc
                if attempt < self.retries:
                    ctx.log(f"bilibili_search 第 {attempt} 次失败，等待重试: {exc}")
                    time.sleep(2 * attempt)

        raise RuntimeError(
            f"搜索 B 站视频失败（已重试 {self.retries} 次）: {last_error}"
        ) from last_error
