"""采集层演示：分别跑 CSV 源和 B 站热门源。

运行：
    python examples/sources_demo.py
"""

import tempfile
import os

import pandas as pd

from pipeline_ai.core import Pipeline
from pipeline_ai.sources import CsvSource, BilibiliHotSource


def demo_csv():
    # 造一份临时 CSV，用完自动删
    df = pd.DataFrame({
        "name": ["A", "B", "C"],
        "score": [90, 80, 70],
    })
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    csv_path = tmp.name
    tmp.close()
    df.to_csv(csv_path, index=False)

    pipeline = Pipeline("csv_demo")
    pipeline.add_stage(CsvSource(csv_path))
    ctx = pipeline.run()
    print("=== CSV 源结果 ===")
    print(ctx.df)
    print(ctx.summary())
    print()
    os.unlink(csv_path)


def demo_bilibili():
    pipeline = Pipeline("bilibili_demo")
    pipeline.add_stage(BilibiliHotSource(limit=10))
    ctx = pipeline.run()
    print("=== B 站热门视频（前 10 条） ===")
    print(ctx.df[["title", "author", "views", "likes"]].to_string(index=False))
    print(ctx.summary())


if __name__ == "__main__":
    demo_csv()
    demo_bilibili()
