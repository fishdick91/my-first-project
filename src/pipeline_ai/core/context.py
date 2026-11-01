"""Pipeline context: shared state passed between stages.

Holds the current DataFrame plus metadata and an execution log
so every stage can see what happened before it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


class Context:
    def __init__(self, name: str = "pipeline") -> None:
        self.name: str = name
        self.started_at: datetime = datetime.now()
        self.df: Optional[pd.DataFrame] = None
        self.metadata: Dict[str, Any] = {}
        self.history: List[str] = []

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}"
        self.history.append(entry)

    def set_source(self, source_name: str, rows: int) -> None:
        self.metadata["source"] = source_name
        self.metadata["initial_rows"] = rows
        self.log(f"source={source_name}, rows={rows}")

    def summary(self) -> str:
        lines = [
            f"Pipeline: {self.name}",
            f"Source:   {self.metadata.get('source', 'n/a')}",
            f"Rows:     {len(self.df) if self.df is not None else 0}",
            f"Duration: {(datetime.now() - self.started_at).total_seconds():.2f}s",
            "--- history ---",
        ]
        lines.extend(self.history)
        return "\n".join(lines)
