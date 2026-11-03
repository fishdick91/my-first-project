"""文件落盘环节：把 DataFrame 存成 CSV。

想在哪个阶段留产物，就把 FileSink 插到哪个阶段后面。
它不修改数据，只负责存盘，然后原样返回 df 供后续环节使用。

路径支持 {date} 占位符，会被替换成当天日期（如 20261103），
这样每天跑一遍不会覆盖前一天的产物：

    FileSink("data/raw/bilibili_hot_{date}.csv")
"""

from __future__ import annotations

import os
from datetime import datetime
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
        path = self.path.replace("{date}", datetime.now().strftime("%Y%m%d"))

        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        df.to_csv(path, **self.to_csv_kwargs)
        ctx.log(f"saved {len(df)} rows -> {path}")
        return df
