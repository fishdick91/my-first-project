"""Pipeline context: shared state passed between stages.

Holds the current DataFrame plus metadata and an execution log
so every stage can see what happened before it.

传入 log_dir 时，日志除了保存在 history 里，还会同步写一份到
logs/<pipeline>_<时间戳>.log，方便事后排查。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd


class Context:
    def __init__(self, name: str = "pipeline", log_dir: Optional[str] = None) -> None:
        self.name: str = name
        self.started_at: datetime = datetime.now()
        self.df: Optional[pd.DataFrame] = None
        self.metadata: Dict[str, Any] = {}
        self.history: List[str] = []

        self.log_path: Optional[str] = None
        self._log_file = None

        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
            self.log_path = os.path.join(log_dir, f"{name}_{stamp}.log")
            self._log_file = open(self.log_path, "a", encoding="utf-8")
            self.log(f"pipeline '{name}' started")

    def log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{stamp}] {message}"
        self.history.append(entry)
        if self._log_file:
            self._log_file.write(entry + "\n")
            self._log_file.flush()

    def close(self) -> None:
        """关闭日志文件句柄。Pipeline.run() 结束时自动调用。"""
        if self._log_file:
            self._log_file.close()
            self._log_file = None

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
        ]
        if self.log_path:
            lines.append(f"Log file: {self.log_path}")
        lines.append("--- history ---")
        lines.extend(self.history)
        return "\n".join(lines)
