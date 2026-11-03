"""可视化演示：采集 → 清洗 → 体检 → 出一页 HTML 图表。

运行：
    python examples/viz_demo.py

产物：
    output/bilibili_hot_<日期>.html   浏览器直接打开就能交互
"""

from pipeline_ai.core import Pipeline
from pipeline_ai.sources import BilibiliHotSource
from pipeline_ai.stages import CleanStage, ProfileStage
from pipeline_ai.viz import VizStage


def main():
    pipeline = Pipeline("viz_demo", log_dir="logs")
    (pipeline
        .add_stage(BilibiliHotSource(limit=30))
        .add_stage(CleanStage(
            drop_na=True,
            drop_duplicates=["bvid"],
            strip_columns=["title"],
        ))
        .add_stage(ProfileStage())
        .add_stage(VizStage(
            output="output/bilibili_hot_{date}.html",
            title="B站热门视频分析",
            charts=[
                {"type": "bar_top", "x": "title", "y": "views", "top": 10,
                 "title": "播放量 Top 10"},
                {"type": "scatter", "x": "views", "y": "likes",
                 "title": "播放量 vs 点赞数"},
                {"type": "line", "x": "pubdate", "y": "views",
                 "title": "各视频播放量（按发布时间排序）"},
            ],
        ))
    )

    ctx = pipeline.run()
    print()
    print(ctx.summary())
    print()
    print(f"打开这个文件看图表: {ctx.metadata['charts']}")


if __name__ == "__main__":
    main()
