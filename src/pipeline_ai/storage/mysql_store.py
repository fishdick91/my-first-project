"""MySQL 存储环节。

凭据从环境变量读取（通过 python-dotenv 从项目根目录 .env 加载），
不在代码里写死密码。

.env 格式（分项）：
    MYSQL_HOST=10.112.183.66
    MYSQL_PORT=3306
    MYSQL_USER=root
    MYSQL_PASSWORD=123456
    MYSQL_DATABASE=data_pipeline

或者直接给一个完整 URL（优先级更高）：
    MYSQL_URL=mysql+pymysql://root:pass@host:3306/db

使用前在脚本入口调用：
    from dotenv import load_dotenv
    load_dotenv()

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

    @staticmethod
    def _build_url():
        from sqlalchemy import URL

        # 完整 URL 优先级最高
        raw = os.getenv("MYSQL_URL")
        if raw:
            return raw

        # 分项配置
        host = os.getenv("MYSQL_HOST")
        port = int(os.getenv("MYSQL_PORT", "3306"))
        user = os.getenv("MYSQL_USER")
        password = os.getenv("MYSQL_PASSWORD", "")
        database = os.getenv("MYSQL_DATABASE")

        if not all([host, user, database]):
            raise RuntimeError(
                "MySQL 凭据未完整配置。请在 .env 中设置："
                "MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE"
            )

        return URL.create(
            "mysql+pymysql",
            username=user,
            password=password,
            host=host,
            port=port,
            database=database,
        )

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        from sqlalchemy import create_engine

        engine = create_engine(self._build_url())
        df.to_sql(self.table, engine, if_exists=self.if_exists, index=False)
        ctx.log(f"saved {len(df)} rows -> mysql table '{self.table}'")
        engine.dispose()
        return df
