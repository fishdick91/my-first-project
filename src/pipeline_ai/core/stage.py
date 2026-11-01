"""Stage base class.

Every step in the pipeline (source, clean, transform, reduce, store, viz)
is a Stage. It receives a DataFrame and the shared Context, returns a
(possibly modified) DataFrame.

Stages are intentionally dumb: they only execute rules, they don't
generate them. The ai/ module is responsible for producing the rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from .context import Context


class Stage(ABC):
    name: str = "stage"

    def __call__(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        ctx.log(f"→ {self.name}")
        result = self.run(df, ctx)
        rows = len(result) if result is not None else 0
        ctx.log(f"← {self.name} done, {rows} rows")
        return result

    @abstractmethod
    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        """Execute the stage. Override this in subclasses."""
        ...
