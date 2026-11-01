"""文件落盘环节：把 DataFrame 存成 CSV。

想在哪个阶段留产物，就把 FileSink 插到哪个阶段后面。
它不修改数据，只负责存盘，然后原样返回 df 供后续环节使用。
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class FileSink(Stage):
    name = "file_sink"

    def __init__(self, path: str, **kwargs: Any) -> None:
        self.path = path
        self.to_csv_kwargs: Dict[str, Any] = {"index": False, **kwargs}

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        df.to_csv(self.path, **self.to_csv_kwargs)
        ctx.log(f"saved {len(df)} rows -> {self.path}")
        return df
