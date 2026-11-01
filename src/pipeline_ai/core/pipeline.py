"""Pipeline orchestrator.

Runs a list of stages in order, threading the DataFrame and shared
Context through each one. New stages are added with .add_stage()
so the pipeline reads top-to-bottom like a recipe.
"""

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from .context import Context
from .stage import Stage


class Pipeline:
    def __init__(self, name: str = "pipeline") -> None:
        self.name = name
        self.stages: List[Stage] = []

    def add_stage(self, stage: Stage) -> "Pipeline":
        self.stages.append(stage)
        return self

    def run(self, df: Optional[pd.DataFrame] = None) -> Context:
        ctx = Context(self.name)
        ctx.df = df
        for stage in self.stages:
            ctx.df = stage(ctx.df, ctx)
        ctx.log("pipeline finished")
        return ctx
