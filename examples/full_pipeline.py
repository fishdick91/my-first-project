"""完整管道演示：异步采集 → DuckDB 中间存储 → 清洗 → 规约 → MySQL 汇总。

运行：
    python examples/full_pipeline.py

中间产物全部落在 data/warehouse.duckdb 的三张表里：
    raw_bilibili_hot      原始数据
    cleaned_bilibili_hot  清洗后
    reduced_bilibili_hot  规约后（按作者聚合）

最终结果写入 MySQL ai数据处理库.bilibili_hot_reduced。
"""

import os

import duckdb
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text

from pipeline_ai.core import Pipeline
from pipeline_ai.sources import AsyncBilibiliHotSource
from pipeline_ai.stages import CleanStage, ReduceStage, ProfileStage
from pipeline_ai.storage import DuckDBStore, MysqlStore

load_dotenv()

WAREHOUSE = "data/warehouse.duckdb"


def main():
    pipeline = Pipeline("bilibili_full", log_dir="logs")
    (pipeline
        .add_stage(AsyncBilibiliHotSource(pages=3, page_size=20, concurrency=3))
        .add_stage(ProfileStage())
        .add_stage(DuckDBStore("raw_bilibili_hot", path=WAREHOUSE))
        .add_stage(CleanStage(
            drop_na=True,
            drop_duplicates=["bvid"],
            strip_columns=["title"],
        ))
        .add_stage(DuckDBStore("cleaned_bilibili_hot", path=WAREHOUSE))
        .add_stage(ReduceStage(
            group_by="author",
            agg={"views": "sum", "likes": "sum", "bvid": "count"},
        ))
        .add_stage(DuckDBStore("reduced_bilibili_hot", path=WAREHOUSE))
        .add_stage(MysqlStore(table="bilibili_hot_reduced", if_exists="replace"))
    )

    ctx = pipeline.run()

    print("\n=== DuckDB 中间结果 ===")
    con = duckdb.connect(WAREHOUSE, read_only=True)
    for table in ("raw_bilibili_hot", "cleaned_bilibili_hot", "reduced_bilibili_hot"):
        rows = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:22s} {rows} rows")
    con.close()

    print("\n=== MySQL 最终结果 ===")
    url = URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE"),
    )
    engine = create_engine(url)
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM bilibili_hot_reduced")).scalar()
        top3 = conn.execute(text(
            "SELECT author, views, likes FROM bilibili_hot_reduced ORDER BY views DESC LIMIT 3"
        )).fetchall()
        print(f"  表 bilibili_hot_reduced 共 {count} 行")
        print("  Top 3 作者（按播放量）:")
        for row in top3:
            print(f"    {row[0]:20s} views={row[1]:>10,}  likes={row[2]:>8,}")
    engine.dispose()

    print()
    print(ctx.summary())


if __name__ == "__main__":
    main()
