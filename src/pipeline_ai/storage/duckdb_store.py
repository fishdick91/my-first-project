"""DuckDB 中间存储环节。

用 DuckDB 文件代替 CSV 做中间落盘：单文件、能直接跑 SQL、读写都比 CSV 快，
而且可以随时用 Python 或 DuckDB CLI 查询中间结果。

数据都存在同一个 .duckdb 文件里，不同阶段各占一张表，
所以三个中间产物变成 warehouse 里的三张表：

    raw_bilibili_hot      原始
    cleaned_bilibili_hot  清洗后
    reduced_bilibili_hot  规约后
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class DuckDBStore(Stage):
    name = "duckdb_store"

    def __init__(
        self,
        table: str,
        path: str = "data/warehouse.duckdb",
        if_exists: str = "replace",
    ) -> None:
        self.table = table
        self.path = path
        self.if_exists = if_exists

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        import duckdb

        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        con = duckdb.connect(self.path)
        try:
            if self.if_exists == "replace":
                con.execute(f"CREATE OR REPLACE TABLE {self.table} AS SELECT * FROM df")
            elif self.if_exists == "append":
                exists = con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    f"WHERE table_name = '{self.table}'"
                ).fetchone()[0]
                if exists:
                    con.execute(f"INSERT INTO {self.table} SELECT * FROM df")
                else:
                    con.execute(f"CREATE TABLE {self.table} AS SELECT * FROM df")
            else:
                raise ValueError(f"if_exists 只能是 replace 或 append，收到 {self.if_exists}")
        finally:
            con.close()

        ctx.log(f"saved {len(df)} rows -> duckdb {self.path}:{self.table}")
        return df
