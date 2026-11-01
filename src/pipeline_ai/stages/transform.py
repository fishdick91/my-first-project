"""变换环节：类型转换与字段派生。

dtype_map: {"col": "int"} 之类的类型转换
derive:    {"new_col": "views * 0.1"} 用 pandas.eval 派生新列
"""

from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class TransformStage(Stage):
    name = "transform"

    def __init__(
        self,
        dtype_map: Optional[Dict[str, str]] = None,
        derive: Optional[Dict[str, str]] = None,
    ) -> None:
        self.dtype_map = dtype_map or {}
        self.derive = derive or {}

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        for col, dtype in self.dtype_map.items():
            if col in df.columns:
                df[col] = df[col].astype(dtype)

        for new_col, expr in self.derive.items():
            df[new_col] = df.eval(expr)

        ctx.log(f"transformed, columns={list(df.columns)}")
        return df
