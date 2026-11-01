"""数据源基类。

Source 是管道的第一个环节：它从外部（文件/API/数据库）读取数据，
返回一个 DataFrame。它继承自 Stage，所以能直接放进 Pipeline。
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class Source(Stage):
    name: str = "source"

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        """子类实现：抓取数据并返回 DataFrame。

        注意：Source 会忽略传入的 df，因为它是数据的起点。
        """
        raise NotImplementedError
