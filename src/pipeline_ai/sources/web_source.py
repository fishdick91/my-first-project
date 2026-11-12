"""通用网页数据源：用户输入任意 URL，AI 自动分析页面提取数据。"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

import pandas as pd

from ..core import Context, Stage
from . import registry
from .web_scraper import scrape_url


class WebSource(Stage):
    """通用网页爬虫源，接收 url 参数，自动提取结构化数据。

    如果提供 keyword 且数据源配置了 search_url，会用搜索 URL 替换 {keyword}。
    """

    def __init__(self, url: str, site: Optional[str] = None, keyword: Optional[str] = None, instruction: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__()
        self.site = site
        self.keyword = keyword
        self.instruction = instruction  # 用户原始需求，传给 AI 指导字段提取

        # 如果有关键词且该站点配置了搜索 URL，用搜索 URL
        if keyword and site:
            cfg = registry.get_source_config(site)
            search_url = cfg.get("search_url") if cfg else None
            if search_url and "{keyword}" in search_url:
                self.url = search_url.replace("{keyword}", quote(keyword))
            else:
                self.url = url
        else:
            self.url = url

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        desc = f"正在抓取网页: {self.url}"
        if self.keyword:
            desc += f"（关键词: {self.keyword}）"
        ctx.log(desc)
        records = scrape_url(self.url, site=self.site, instruction=self.instruction)
        if not records:
            ctx.log("⚠️ 未提取到数据")
            return pd.DataFrame()
        result = pd.DataFrame(records)
        ctx.log(f"✅ 提取到 {len(result)} 行，{len(result.columns)} 列: {list(result.columns)}")
        return result
