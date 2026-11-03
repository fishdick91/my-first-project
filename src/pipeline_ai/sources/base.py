"""数据源基类。

Source 是管道的第一个环节：它从外部（文件/API/数据库）读取数据，
返回一个 DataFrame。它继承自 Stage，所以能直接放进 Pipeline。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

import pandas as pd
import requests

from ..core.stage import Stage
from ..core.context import Context


def request_with_retry(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
    retries: int = 3,
    backoff: float = 1.0,
) -> requests.Response:
    """GET 请求，失败自动重试。

    每次重试的等待时间是 backoff * 尝试次数（1s、2s、3s……），
    全部失败后抛出 RuntimeError，并把原始异常挂在 __cause__ 上。
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(backoff * attempt)

    raise RuntimeError(f"请求失败（已尝试 {retries} 次）: {url}") from last_error


class Source(Stage):
    name: str = "source"

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        """子类实现：抓取数据并返回 DataFrame。

        注意：Source 会忽略传入的 df，因为它是数据的起点。
        """
        raise NotImplementedError
