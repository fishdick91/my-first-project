"""完整管道演示：采集 → 清洗 → 规约 → 各阶段落盘 CSV。

运行：
    python examples/full_pipeline.py

跑完后查看 data/raw、data/cleaned、data/reduced 三个目录里的 CSV。
"""

import os

from dotenv import load_dotenv

from pipeline_ai.core import Pipeline
from pipeline_ai.sources import BilibiliHotSource
from pipeline_ai.stages import CleanStage, ReduceStage, FileSink

# 加载 .env（MySQL 等密钥从这里读，没有也不影响 CSV 流程）
load_dotenv()


def main():
    pipeline = Pipeline("bilibili_full")
    (pipeline
        # 1. 采集 B 站热门视频 30 条
        .add_stage(BilibiliHotSource(limit=30))

        # 2. 原始数据落盘
        .add_stage(FileSink("data/raw/bilibili_hot.csv"))

        # 3. 清洗：删空值、按 bvid 去重、标题去空格
        .add_stage(CleanStage(
            drop_na=True,
            drop_duplicates=["bvid"],
            strip_columns=["title"],
        ))

        # 4. 清洗后落盘
        .add_stage(FileSink("data/cleaned/bilibili_hot.csv"))

        # 5. 规约：按作者聚合，统计总播放、总点赞、视频数
        .add_stage(ReduceStage(
            group_by="author",
            agg={"views": "sum", "likes": "sum", "bvid": "count"},
        ))

        # 6. 规约后落盘
        .add_stage(FileSink("data/reduced/bilibili_hot.csv"))
    )

    ctx = pipeline.run()

    print("\n=== 规约结果（按作者聚合）===")
    print(ctx.df.head(10).to_string(index=False))
    print()
    print(ctx.summary())


if __name__ == "__main__":
    main()
