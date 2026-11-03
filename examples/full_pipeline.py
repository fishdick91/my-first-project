"""完整管道演示：采集 → 清洗 → 规约 → CSV 落盘 → 入 MySQL。

运行：
    python examples/full_pipeline.py

跑完后：
    data/raw/bilibili_hot.csv      原始数据
    data/cleaned/bilibili_hot.csv  清洗后
    data/reduced/bilibili_hot.csv  规约后
    MySQL ai数据处理库.bilibili_hot_reduced 表
"""

from dotenv import load_dotenv

from pipeline_ai.core import Pipeline
from pipeline_ai.sources import BilibiliHotSource
from pipeline_ai.stages import CleanStage, ReduceStage, FileSink
from pipeline_ai.storage import MysqlStore

load_dotenv()


def main():
    pipeline = Pipeline("bilibili_full")
    (pipeline
        .add_stage(BilibiliHotSource(limit=30))
        .add_stage(FileSink("data/raw/bilibili_hot.csv"))
        .add_stage(CleanStage(
            drop_na=True,
            drop_duplicates=["bvid"],
            strip_columns=["title"],
        ))
        .add_stage(FileSink("data/cleaned/bilibili_hot.csv"))
        .add_stage(ReduceStage(
            group_by="author",
            agg={"views": "sum", "likes": "sum", "bvid": "count"},
        ))
        .add_stage(FileSink("data/reduced/bilibili_hot.csv"))
        .add_stage(MysqlStore(table="bilibili_hot_reduced", if_exists="replace"))
    )

    ctx = pipeline.run()

    print("\n=== MySQL 验证 ===")
    from sqlalchemy import create_engine, text
    engine = create_engine("mysql+pymysql://root:123456@127.0.0.1:3306/ai数据处理库")
    with engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM bilibili_hot_reduced")).scalar()
        top3 = conn.execute(text(
            "SELECT author, views, likes FROM bilibili_hot_reduced ORDER BY views DESC LIMIT 3"
        )).fetchall()
        print(f"表 bilibili_hot_reduced 共 {count} 行")
        print("Top 3 作者（按播放量）:")
        for row in top3:
            print(f"  {row[0]:20s} views={row[1]:>10,}  likes={row[2]:>8,}")
    engine.dispose()

    print()
    print(ctx.summary())


if __name__ == "__main__":
    main()
