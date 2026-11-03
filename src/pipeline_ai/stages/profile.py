"""数据体检环节：给数据做一次体检，产出结构化的画像。

输出内容：
- 总行数、列数
- 空值单元格数、重复行数
- 每个字段的类型、空值数、不同值个数、数值范围或最长长度

画像会同时写进 ctx.metadata["profile"]，以后接 AI 时，
这坨 JSON 稍加整理就能直接喂给模型，让它据此生成清洗规则。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class ProfileStage(Stage):
    name = "profile"

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        if df is None or df.empty:
            ctx.log("profile: 空数据，跳过")
            return df

        profile = self._profile(df)
        ctx.metadata["profile"] = profile

        ctx.log(
            f"profile: {profile['rows']} rows x {profile['columns']} cols, "
            f"nulls={profile['null_cells']}, duplicates={profile['duplicate_rows']}"
        )
        for col, info in profile["fields"].items():
            extra = ""
            if "min" in info:
                extra = f", range {info['min']}~{info['max']}"
            elif "max_len" in info:
                extra = f", max_len={info['max_len']}"
            ctx.log(
                f"  {col}: {info['dtype']}, nulls={info['nulls']}, "
                f"distinct={info['distinct']}{extra}"
            )

        return df

    @staticmethod
    def _profile(df: pd.DataFrame) -> Dict[str, Any]:
        fields: Dict[str, Dict[str, Any]] = {}

        for col in df.columns:
            series = df[col]
            info: Dict[str, Any] = {
                "dtype": str(series.dtype),
                "nulls": int(series.isna().sum()),
                "distinct": int(series.nunique(dropna=True)),
            }

            if pd.api.types.is_numeric_dtype(series) and series.notna().any():
                info["min"] = series.min().item()
                info["max"] = series.max().item()
            elif not pd.api.types.is_datetime64_any_dtype(series):
                info["max_len"] = int(series.astype(str).str.len().max())

            fields[col] = info

        return {
            "rows": int(len(df)),
            "columns": int(len(df.columns)),
            "null_cells": int(df.isna().sum().sum()),
            "duplicate_rows": int(df.duplicated().sum()),
            "fields": fields,
        }
