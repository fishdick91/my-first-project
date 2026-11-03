"""Pipeline orchestrator.

Runs a list of stages in order, threading the DataFrame and shared
Context through each one. New stages are added with .add_stage()
so the pipeline reads top-to-bottom like a recipe.

传入 log_dir 时，整条管道的执行日志会同步写进文件。
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .context import Context
from .stage import Stage


class Pipeline:
    def __init__(self, name: str = "pipeline", log_dir: Optional[str] = None) -> None:
        self.name = name
        self.log_dir = log_dir
        self.stages: List[Stage] = []

    def add_stage(self, stage: Stage) -> "Pipeline":
        self.stages.append(stage)
        return self

    def run(self, df: Optional[pd.DataFrame] = None) -> Context:
        ctx = Context(self.name, log_dir=self.log_dir)
        ctx.df = df
        try:
            for stage in self.stages:
                ctx.df = stage(ctx.df, ctx)
            ctx.log("pipeline finished")
        finally:
            # 不管中间是否抛错，都别把文件句柄留着
            ctx.close()
        return ctx
