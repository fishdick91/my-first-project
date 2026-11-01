"""CSV 文件数据源。"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from .base import Source
from ..core.context import Context


class CsvSource(Source):
    name = "csv_source"

    def __init__(self, path: str, **kwargs: Any) -> None:
        self.path = path
        self.read_kwargs: Dict[str, Any] = kwargs

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        result = pd.read_csv(self.path, **self.read_kwargs)
        ctx.set_source(f"csv:{self.path}", len(result))
        return result
