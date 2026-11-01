"""清洗环节：处理脏数据。

目前支持：删空值、去重、字符串去空格。
以后接 AI 时，AI 会生成这些规则的参数。
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class CleanStage(Stage):
    name = "clean"

    def __init__(
        self,
        drop_na: bool = True,
        drop_duplicates: Optional[List[str]] = None,
        strip_columns: Optional[List[str]] = None,
    ) -> None:
        self.drop_na = drop_na
        self.drop_duplicates = drop_duplicates
        self.strip_columns = strip_columns or []

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        before = len(df)

        if self.drop_na:
            df = df.dropna()

        if self.drop_duplicates:
            df = df.drop_duplicates(subset=self.drop_duplicates)

        for col in self.strip_columns:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        ctx.log(f"cleaned {before} -> {len(df)} rows")
        return df.reset_index(drop=True)
