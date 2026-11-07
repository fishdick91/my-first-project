"""豆瓣电影 Top250 数据源。

列表页：https://movie.douban.com/top250?start=0  （每页 25 部）
详情页评语：https://movie.douban.com/subject/{id}/  （抓热门短评）

字段：title（电影名）、director（导演）、actors（主演）、quote（评语/短评）、rating（评分）
"""

from __future__ import annotations

import re
import time
from typing import List, Optional

import pandas as pd

from .base import Source, request_with_retry
from . import cookie_store
from ..core.context import Context


# 列表页：匹配一个 <div class="item">...</div> 块
ITEM_RE = re.compile(r'<div class="item">(.*?)</li>', re.DOTALL)
# 电影链接里的 subject id：subject/1292052/
SUBJECT_ID_RE = re.compile(r'subject/(\d+)/')
# 电影名：<span class="title">中文名</span>
TITLE_RE = re.compile(r'<span class="title">([^<]+)</span>')
# 导演
DIRECTOR_RE = re.compile(r'导演:\s*([^&<]+?)(?:&nbsp;|\s+主演:|<)')
# 主演（列表页可能只显示前几位）
ACTOR_RE = re.compile(r'主演:\s*([^<]+?)(?:<|$)')
# 评分
RATING_RE = re.compile(r'<span class="rating_num"[^>]*>([\d.]+)</span>')

# 详情页：热门短评 <span class="short">内容</span>
SHORT_RE = re.compile(r'<span class="short">([^<]*)</span>')


class DoubanTopSource(Source):
    name = "douban_top"

    BASE_URL = "https://movie.douban.com/top250"
    DETAIL_URL = "https://movie.douban.com/subject/{id}/"

    # 默认 UA，Cookie 从 cookie_store 动态注入
    DEFAULT_HEADERS = {
        "User-Agent": cookie_store.DEFAULT_UA,
        "Referer": "https://movie.douban.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    def __init__(
        self,
        limit: int = 50,
        with_review: bool = False,
        timeout: int = 15,
        retries: int = 3,
    ) -> None:
        self.limit = limit
        self.with_review = with_review
        self.timeout = timeout
        self.retries = retries

    @property
    def headers(self) -> dict:
        """动态构造请求头：有 cookie 就带上，没有就裸抓。"""
        return cookie_store.build_headers("douban", self.DEFAULT_HEADERS)

    def _fetch_list_page(self, start: int, ctx: Context) -> List[dict]:
        for attempt in range(1, self.retries + 1):
            try:
                resp = request_with_retry(
                    self.BASE_URL,
                    params={"start": start},
                    headers=self.headers,
                    timeout=self.timeout,
                    retries=1,
                )
                rows = self._parse_list(resp.text)
                if not rows:
                    raise RuntimeError("列表页解析为空，可能被反爬拦截")
                return rows
            except Exception as exc:
                if attempt < self.retries:
                    ctx.log(f"douban_top 列表第 {start} 条第 {attempt} 次失败，重试: {exc}")
                    time.sleep(2 * attempt)
                else:
                    raise RuntimeError(f"抓取豆瓣列表失败: {exc}") from exc
        return []

    def _parse_list(self, html: str) -> List[dict]:
        rows = []
        for block in ITEM_RE.findall(html):
            title_m = TITLE_RE.search(block)
            director_m = DIRECTOR_RE.search(block)
            actor_m = ACTOR_RE.search(block)
            rating_m = RATING_RE.search(block)
            subject_m = SUBJECT_ID_RE.search(block)

            if not title_m:
                continue

            director = director_m.group(1).strip() if director_m else ""
            director = director.split()[0] if director else ""
            actors = actor_m.group(1).strip() if actor_m else ""

            rows.append({
                "subject_id": subject_m.group(1) if subject_m else "",
                "title": title_m.group(1).strip(),
                "director": director,
                "actors": actors,
                "quote": "",
                "rating": float(rating_m.group(1)) if rating_m else 0.0,
            })
        return rows

    def _fetch_review(self, subject_id: str, ctx: Context) -> str:
        """进详情页抓第一条热门短评。"""
        if not subject_id:
            return ""
        try:
            resp = request_with_retry(
                self.DETAIL_URL.format(id=subject_id),
                headers=self.headers,
                timeout=self.timeout,
                retries=2,
            )
            m = SHORT_RE.search(resp.text)
            return m.group(1).strip() if m else ""
        except Exception as exc:
            ctx.log(f"douban_top 评语抓取失败 {subject_id}: {exc}")
            return ""

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        pages = (self.limit + 24) // 25
        all_rows: List[dict] = []

        for page in range(pages):
            start = page * 25
            rows = self._fetch_list_page(start, ctx)
            all_rows.extend(rows)
            ctx.log(f"douban_top 第 {page + 1}/{pages} 页，抓到 {len(rows)} 部")
            if page < pages - 1:
                time.sleep(1)

        all_rows = all_rows[: self.limit]

        # 逐个进详情页抓评语
        if self.with_review:
            ctx.log(f"douban_top 开始抓取 {len(all_rows)} 部电影的评语…")
            for i, row in enumerate(all_rows, 1):
                row["quote"] = self._fetch_review(row["subject_id"], ctx)
                if i % 10 == 0:
                    ctx.log(f"douban_top 评语进度: {i}/{len(all_rows)}")
                time.sleep(0.5)  # 间隔，避免被封

        result = pd.DataFrame(all_rows)
        # subject_id 仅内部用，不输出
        result = result.drop(columns=["subject_id"], errors="ignore")
        ctx.set_source(f"douban_top:{len(result)}", len(result))
        return result
