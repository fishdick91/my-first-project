"""最小可运行示例：验证 core 引擎能跑通。

运行前先安装项目（开发模式）：
    pip install -e .
然后：
    python examples/quickstart.py
"""

import pandas as pd

from pipeline_ai.core import Pipeline, Stage, Context


# --- 自定义环节：删空值 ---
class CleanStage(Stage):
    name = "clean"

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        return df.dropna()


# --- 自定义环节：新增一列 score ---
class ScoreStage(Stage):
    name = "add_score"

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        df = df.copy()
        df["score"] = df["views"] * 0.1
        return df


if __name__ == "__main__":
    data = pd.DataFrame({
        "title": ["A", "B", "C", None],
        "views": [100, 200, 300, 400],
    })

    pipeline = Pipeline("demo")
    (pipeline
        .add_stage(CleanStage())
        .add_stage(ScoreStage()))

    ctx = pipeline.run(data)

    print("=== 结果数据 ===")
    print(ctx.df)
    print()
    print(ctx.summary())
