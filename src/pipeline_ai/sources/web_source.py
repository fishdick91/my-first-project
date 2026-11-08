"""通用网页数据源：用户输入任意 URL，AI 自动分析页面提取数据。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..core import Context, Stage
from .web_scraper import scrape_url


class WebSource(Stage):
    """通用网页爬虫源，接收 url 参数，自动提取结构化数据。"""

    def __init__(self, url: str, site: Optional[str] = None, **kwargs: Any) -> None:
        super().__init__()
        self.url = url
        self.site = site

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        self._log(ctx, f"正在抓取网页: {self.url}")
        records = scrape_url(self.url, site=self.site)
        if not records:
            self._log(ctx, "⚠️ 未提取到数据")
            return pd.DataFrame()
        result = pd.DataFrame(records)
        self._log(ctx, f"✅ 提取到 {len(result)} 行，{len(result.columns)} 列: {list(result.columns)}")
        return result
