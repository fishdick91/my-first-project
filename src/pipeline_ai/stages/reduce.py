"""规约环节：聚合 / 降维。

把明细数据按某个维度聚合，压缩数据量。
例如按作者聚合，算出每个作者的总播放、总点赞、视频数。
"""

from __future__ import annotations

from typing import Dict, List, Union

import pandas as pd

from ..core.stage import Stage
from ..core.context import Context


class ReduceStage(Stage):
    name = "reduce"

    def __init__(
        self,
        group_by: Union[str, List[str]],
        agg: Dict[str, Union[str, List[str]]],
    ) -> None:
        self.group_by = group_by
        self.agg = agg

    def run(self, df: pd.DataFrame, ctx: Context) -> pd.DataFrame:
        before = len(df)
        result = df.groupby(self.group_by, as_index=False).agg(self.agg)
        ctx.log(f"reduced {before} -> {len(result)} rows, group_by={self.group_by}")
        return result
