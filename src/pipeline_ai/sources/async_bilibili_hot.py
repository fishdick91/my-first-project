"""B 站热门视频数据源（异步版）。

和同步版的区别：翻页时并发请求。
比如 pages=3 会同时发 3 个请求，总耗时接近最慢的那一页，
而不是三页相加。解析逻辑复用 bilibili_hot.parse_videos()。

Pipeline 本身是同步的，所以这里用 asyncio.run() 把异步函数"包"成同步
调用，对外仍然是一个普通的 Stage。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import aiohttp
import pandas as pd

from .base import Source
from .bilibili_hot import BilibiliHotSource, parse_videos
from ..core.context import Context


class AsyncBilibiliHotSource(Source):
    name = "bilibili_hot_async"

    def __init__(
        self,
        pages: int = 3,
        page_size: int = 20,
        concurrency: int = 3,
        timeout: int = 10,
        retries: int = 3,
    ) -> None:
        self.pages = pages
        self.page_size = page_size
        self.concurrency = concurrency
        self.timeout = timeout
        self.retries = retries

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        videos = asyncio.run(self._fetch_all_pages())

        # 翻页之间可能返回重复视频，按 bvid 去重
        seen: set[str] = set()
        unique: List[Dict[str, Any]] = []
        for v in videos:
            key = v.get("bvid")
            if key and key not in seen:
                seen.add(key)
                unique.append(v)

        result = parse_videos(unique)
        ctx.set_source(f"bilibili_hot_async:{self.pages}pages", len(result))
        ctx.log(f"async fetched {len(videos)} videos, {len(result)} after dedup")
        return result

    async def _fetch_all_pages(self) -> List[Dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.concurrency)
        async with aiohttp.ClientSession(headers=BilibiliHotSource.HEADERS) as session:
            tasks = [
                self._fetch_page(session, semaphore, page)
                for page in range(1, self.pages + 1)
            ]
            page_results = await asyncio.gather(*tasks)

        return [video for page in page_results for video in page]

    async def _fetch_page(
        self,
        session: aiohttp.ClientSession,
        semaphore: asyncio.Semaphore,
        page: int,
    ) -> List[Dict[str, Any]]:
        params = {"ps": self.page_size, "pn": page}
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with semaphore:
            for attempt in range(1, self.retries + 1):
                try:
                    async with session.get(
                        BilibiliHotSource.API_URL, params=params, timeout=timeout
                    ) as resp:
                        resp.raise_for_status()
                        payload = await resp.json(content_type=None)

                    if payload.get("code") != 0:
                        raise RuntimeError(f"Bilibili API error: {payload.get('message')}")

                    return payload["data"]["list"]
                except Exception:
                    if attempt == self.retries:
                        raise
                    await asyncio.sleep(attempt)

        return []
