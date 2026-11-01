"""MySQL 存储环节。

连接串从环境变量 MYSQL_URL 读取，不在代码里写密码。
格式：mysql+pymysql://用户名:密码@主机:端口/数据库名

使用前先在项目根目录建 .env：
    MYSQL_URL=mysql+pymysql://root:yourpass@localhost:3306/data_pipeline

本环节依赖 SQLAlchemy + pymysql。
"""

from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class MysqlStore(Stage):
    name = "mysql_store"

    def __init__(self, table: str, if_exists: str = "replace") -> None:
        self.table = table
        self.if_exists = if_exists

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        # 延迟导入，避免没装 SQLAlchemy 时影响其他环节
        from sqlalchemy import create_engine

        url = os.getenv("MYSQL_URL")
        if not url:
            raise RuntimeError(
                "MYSQL_URL 未配置。请在项目根目录创建 .env 文件，"
                "内容如：MYSQL_URL=mysql+pymysql://user:pass@host:3306/dbname"
            )

        engine = create_engine(url)
        df.to_sql(self.table, engine, if_exists=self.if_exists, index=False)
        ctx.log(f"saved {len(df)} rows -> mysql table '{self.table}'")
        engine.dispose()
        return df
