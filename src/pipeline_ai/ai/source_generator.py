"""AI 数据源配置生成器。

用户用自然语言描述想爬什么（网站、URL、字段），AI 生成 YAML 配置片段，
保存到 config/sources.yaml 后即可使用，无需写代码。

也支持自动探测：用户只给 URL，系统自动请求并找出所有字段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests
import yaml

from .llm import LLMClient, extract_json


def detect_fields(url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """自动探测 JSON API 的字段。

    用户只提供 URL，系统自动请求，找出：
    - 数组在 JSON 的哪个路径（data_path）
    - 每条记录有哪些字段

    返回 {"data_path": ..., "fields": [...]}
    """
    default_headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    if headers:
        default_headers.update(headers)

    resp = requests.get(url, params=params, headers=default_headers, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    # 找数组路径（BFS）
    data_path, records = _find_array(payload)
    if records is None:
        raise RuntimeError("返回的 JSON 里没找到数组")

    # 取第一条记录的字段
    if not records:
        raise RuntimeError("数组为空")

    first = records[0]
    fields = []
    if isinstance(first, dict):
        for k, v in first.items():
            if isinstance(v, (dict, list)):
                # 嵌套字段标记一下
                fields.append(f"{k}（嵌套）")
            else:
                fields.append(k)

    return {
        "data_path": data_path,
        "fields": fields,
        "sample_count": len(records),
    }


def _find_array(obj: Any, path: str = "") -> tuple:
    """BFS 找 JSON 里第一个数组，返回 (路径, 数组)。"""
    from collections import deque
    queue = deque([(obj, "")])
    while queue:
        cur, cur_path = queue.popleft()
        if isinstance(cur, list):
            return (cur_path or "root", cur)
        if isinstance(cur, dict):
            for k, v in cur.items():
                new_path = f"{cur_path}.{k}" if cur_path else k
                if isinstance(v, list):
                    return (new_path, v)
                queue.append((v, new_path))
    return ("", None)

_SYSTEM_PROMPT = """你是一个爬虫配置助手。用户会描述想爬取的网站，你要生成一份数据源配置 JSON。

配置格式说明（必须严格遵守）：
{
  "id": "数据源英文ID（如 zhihu_hot）",
  "label": "中文标签",
  "fetcher": "http_json 或 http_html",
  "url": "请求的 URL",
  "params": {"默认查询参数，可为空对象"},
  "data_path": "JSON 数组路径（仅 http_json，如 data.list）",
  "list_selector": "CSS selector（仅 http_html，每条记录的容器）",
  "fields": {"输出列名": "JSON路径 或 CSS selector"},
  "headers": {"请求头，可为空"},
  "params_overridable": {
    "limit": {"target": "ps", "default": 20, "min": 1, "max": 100}
  },
  "error_check": "code != 0（可选，仅 http_json）",
  "pagination": {"type": "offset", "param": "start", "step": 25, "max": 250}（可选，仅 http_html）
}

规则：
1. 只输出 JSON，不要解释。
2. fetcher 选 http_json（如果是 API 返回 JSON）或 http_html（如果是网页）。
3. fields 的值：http_json 用点号路径（如 owner.name），http_html 用 CSS selector。
4. 对于 http_html，如果字段需要正则提取，用 {"selector": "...", "extract": "正则"}。
5. params_overridable 里定义用户可覆盖的参数，比如 limit。
6. 如果不确定字段，宁可不填也不要瞎编。
"""


def generate_source_config(description: str) -> Dict[str, Any]:
    """让 AI 根据用户描述生成数据源配置。"""
    client = LLMClient()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": description},
    ]
    text = client.chat(messages, temperature=0.0, max_tokens=1024)
    return extract_json(text)


def config_to_yaml(config: Dict[str, Any]) -> str:
    """把配置 dict 转成 YAML 字符串（带注释友好的格式）。"""
    return yaml.dump(config, allow_unicode=True, sort_keys=False)


def save_source_config(source_id: str, config: Dict[str, Any]) -> str:
    """把配置保存到 config/sources.yaml，返回保存路径。"""
    import os
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
        "config", "sources.yaml",
    )
    config_path = os.path.normpath(config_path)

    # 读取现有配置
    if os.path.exists(config_path):
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {"sources": {}}

    data.setdefault("sources", {})
    data["sources"][source_id] = config

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    return config_path
