"""通用数据源：根据 YAML 配置执行抓取，不写硬编码类。

支持两种 fetcher：
- http_json：JSON API，字段映射用点号路径（如 owner.name）
- http_html：HTML 页面，字段映射用 CSS selector
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import Source, request_with_retry
from . import cookie_store
from ..core.context import Context


def _get_nested(obj: Any, path: str) -> Any:
    """按点号路径从嵌套 dict/list 里取值，如 'data.list[0].title'。"""
    current = obj
    for part in path.split("."):
        # 处理 list 索引：list[0]
        m = re.match(r"^([^\[]+)\[(\d+)\]$", part)
        if m:
            key, idx = m.group(1), int(m.group(2))
            current = (current or {}).get(key)
            if isinstance(current, list) and len(current) > idx:
                current = current[idx]
            else:
                return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
    return current


def _check_error(payload: Any, check_expr: str) -> Optional[str]:
    """简易错误检查：支持 'code != 0' 形式，返回错误信息或 None。"""
    if not check_expr or not isinstance(payload, dict):
        return None
    # 解析 "code != 0" 或 "code == -400"
    m = re.match(r"^(\w+)\s*(==|!=)\s*(.+)$", check_expr.strip())
    if not m:
        return None
    field, op, expected = m.groups()
    actual = payload.get(field)
    expected_val: Any = expected
    try:
        expected_val = int(expected)
    except ValueError:
        try:
            expected_val = float(expected)
        except ValueError:
            pass
    if op == "!=" and actual != expected_val:
        return f"{field}={actual} (expected != {expected_val})"
    if op == "==" and actual == expected_val:
        return f"{field}={actual} (expected == {expected_val})"
    return None


class GenericSource(Source):
    """通用数据源，根据 registry 配置执行。"""

    name = "generic"

    def __init__(self, source_type: str, config: Optional[Dict[str, Any]] = None, **user_params: Any) -> None:
        """初始化通用数据源。

        Args:
            source_type: 数据源 ID（用于查注册表）
            config: 直接传入配置（用于测试未保存的配置），不查注册表
            **user_params: 用户覆盖的参数（如 limit, keyword）
        """
        if config is not None:
            self.config = config
        else:
            from .registry import get_source_config
            self.config = get_source_config(source_type)
            if self.config is None:
                raise ValueError(f"未注册的数据源: {source_type}")
        self.source_type = source_type
        self.user_params = user_params
        self.timeout = self.config.get("timeout", 15)
        self.retries = self.config.get("retries", 3)

    @property
    def headers(self) -> dict:
        base = {"User-Agent": cookie_store.DEFAULT_UA}
        base.update(self.config.get("headers", {}))
        cookie_site = self.config.get("cookie_site")
        if cookie_site:
            return cookie_store.build_headers(cookie_site, base)
        return base

    def _build_params(self) -> Dict[str, Any]:
        """合并默认 params 和用户覆盖的 params。"""
        params = dict(self.config.get("params", {}))
        overridable = self.config.get("params_overridable", {})
        for key, val in self.user_params.items():
            if key in overridable:
                target = overridable[key]["target"]
                if target.startswith("__"):
                    continue  # 特殊参数，单独处理
                params[target] = val
        return params

    def _get_special(self, key: str, default: Any = None) -> Any:
        """获取特殊参数（如 __limit__, __with_review__）。"""
        overridable = self.config.get("params_overridable", {})
        if key in overridable:
            target = overridable[key]["target"]
            if target == f"__{key}__":
                return self.user_params.get(key, overridable[key].get("default", default))
        return self.user_params.get(key, default)

    def _http_json(self, ctx: Context) -> pd.DataFrame:
        """JSON API 抓取。"""
        params = self._build_params()
        error_check = self.config.get("error_check")
        data_path = self.config.get("data_path", "")
        fields = self.config.get("fields", {})

        for attempt in range(1, self.retries + 1):
            try:
                resp = request_with_retry(
                    self.config["url"],
                    params=params,
                    headers=self.headers,
                    timeout=self.timeout,
                    retries=1,
                )
                payload = resp.json()

                err = _check_error(payload, error_check)
                if err:
                    raise RuntimeError(f"API 返回错误: {err}")

                records = _get_nested(payload, data_path)
                if not isinstance(records, list):
                    raise RuntimeError(f"data_path '{data_path}' 不是数组")

                rows = []
                for item in records:
                    if fields:
                        # 指定了字段映射：按映射取值
                        row = {}
                        for out_col, path in fields.items():
                            val = _get_nested(item, path)
                            if isinstance(val, str):
                                val = re.sub(r"<[^>]+>", "", val)
                            row[out_col] = val
                    else:
                        # 没指定字段：抓所有顶层字段（嵌套的转成字符串）
                        row = {}
                        for k, v in item.items():
                            if isinstance(v, (dict, list)):
                                row[k] = str(v)[:500]
                            elif isinstance(v, str):
                                row[k] = re.sub(r"<[^>]+>", "", v)
                            else:
                                row[k] = v
                    rows.append(row)

                # 处理特殊参数 limit
                limit = self._get_special("limit")
                if limit:
                    rows = rows[: int(limit)]

                df = pd.DataFrame(rows)
                ctx.set_source(f"{self.source_type}:{len(df)}", len(df))
                return df

            except Exception as exc:
                if attempt < self.retries:
                    ctx.log(f"{self.source_type} 第 {attempt} 次失败，重试: {exc}")
                    time.sleep(2 * attempt)
                else:
                    raise RuntimeError(f"抓取 {self.source_type} 失败: {exc}") from exc

        return pd.DataFrame()

    def _http_html(self, ctx: Context) -> pd.DataFrame:
        """HTML 页面抓取（CSS selector）。"""
        from lxml import html as lxml_html

        url = self.config["url"]
        pagination = self.config.get("pagination")
        list_selector = self.config.get("list_selector", "")
        fields = self.config.get("fields", {})
        limit = self._get_special("limit", 50)

        all_rows: List[dict] = []

        # 分页
        if pagination and pagination.get("type") == "offset":
            step = pagination.get("step", 25)
            max_start = pagination.get("max", 250)
            param = pagination.get("param", "start")
            pages = (int(limit) + step - 1) // step
            for page in range(pages):
                start = page * step
                if start > max_start:
                    break
                rows = self._fetch_html_page(url, {param: start}, list_selector, fields, lxml_html, ctx)
                all_rows.extend(rows)
                if page < pages - 1:
                    time.sleep(1)
        else:
            all_rows = self._fetch_html_page(url, {}, list_selector, fields, lxml_html, ctx)

        all_rows = all_rows[: int(limit)]

        # 特殊处理：with_review 抓详情页评语
        if self._get_special("with_review", False):
            self._fetch_reviews(all_rows, ctx)

        df = pd.DataFrame(all_rows)
        ctx.set_source(f"{self.source_type}:{len(df)}", len(df))
        return df

    def _fetch_html_page(
        self,
        url: str,
        params: Dict[str, Any],
        list_selector: str,
        fields: Dict[str, str],
        lxml_html: Any,
        ctx: Context,
    ) -> List[dict]:
        resp = request_with_retry(
            url, params=params, headers=self.headers,
            timeout=self.timeout, retries=2,
        )
        tree = lxml_html.fromstring(resp.text)
        items = tree.cssselect(list_selector)

        rows = []
        for item in items:
            row = {}
            for out_col, field_cfg in fields.items():
                # 支持两种配置：字符串（纯 selector）或 dict（selector + extract）
                if isinstance(field_cfg, str):
                    selector, extract_re = field_cfg, None
                else:
                    selector = field_cfg.get("selector", "")
                    extract_re = field_cfg.get("extract")

                els = item.cssselect(selector)
                if els:
                    text = els[0].text_content().strip()
                    if extract_re:
                        m = re.search(extract_re, text)
                        row[out_col] = m.group(1).strip() if m else ""
                    else:
                        row[out_col] = text
                else:
                    row[out_col] = ""
            rows.append(row)
        return rows

    def _fetch_reviews(self, rows: List[dict], ctx: Context) -> None:
        """豆瓣详情页评语抓取（特殊逻辑，暂时硬编码）。"""
        # TODO: 这块逻辑比较特殊，后续可以抽到配置里
        pass

    def run(self, df: Optional[pd.DataFrame], ctx: Context) -> pd.DataFrame:
        fetcher = self.config.get("fetcher", "http_json")
        if fetcher == "http_json":
            return self._http_json(ctx)
        elif fetcher == "http_html":
            return self._http_html(ctx)
        else:
            raise ValueError(f"不支持的 fetcher: {fetcher}")
