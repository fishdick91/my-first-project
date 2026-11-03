"""LLM 客户端：调用 OpenAI 兼容的 /chat/completions 接口。

为什么不用 openai SDK？
    只用 requests 发一个 POST 就够了，少装一个依赖，
    也让"换供应商"变成纯粹改 .env 的事。

统一三个环境变量（换模型只改 .env，不改代码）：

    LLM_BASE_URL   https://open.bigmodel.cn/api/paas/v4
    LLM_API_KEY    智谱的 API Key
    LLM_MODEL      glm-4-flash

任何 OpenAI 兼容的服务（DeepSeek、通义千问、Moonshot……）
只要 base_url 和 model 对得上，都能直接用。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

try:
    # 本地跑脚本时自动读取项目根目录的 .env；
    # 没装 python-dotenv 也不影响，环境变量照样能用
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass


DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-4-flash"


class LLMError(RuntimeError):
    """调用模型失败（缺 key、网络错误、返回格式不对等）。"""


def extract_json(text: str) -> Any:
    """从模型回复里抠出 JSON。

    模型有时会不听话，把 JSON 包在 ```json 围栏里，
    或者在前后加一句"好的，这是结果："。这里都做兼容。
    """
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 兜底：取第一个 { 到最后一个 } 之间的内容
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise LLMError(f"模型返回的不是合法 JSON: {exc}") from exc
        raise LLMError(f"模型返回里找不到 JSON: {text[:200]}")


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: int = 60,
        retries: int = 2,
    ) -> None:
        self.base_url = (base_url or os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.retries = retries

        if not self.api_key:
            raise LLMError(
                "没有找到 LLM_API_KEY。请在项目根目录的 .env 里写上：\n"
                "  LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4\n"
                "  LLM_API_KEY=你的智谱Key\n"
                "  LLM_MODEL=glm-4-flash"
            )

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.2,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
    ) -> str:
        """发一轮对话，返回模型回复的纯文本。"""
        url = f"{self.base_url}/chat/completions"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload, timeout=self.timeout
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise LLMError(
                        f"模型服务返回 {resp.status_code}: {resp.text[:200]}"
                    )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except (requests.RequestException, LLMError, KeyError, IndexError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(2 * attempt)

        raise LLMError(f"调用模型失败（已尝试 {self.retries} 次）: {last_error}") from last_error

    def chat_json(
        self, messages: List[Dict[str, str]], **kwargs: Any
    ) -> Dict[str, Any]:
        """要求模型返回一个 JSON 对象，并解析好。"""
        kwargs.setdefault("json_mode", True)
        text = self.chat(messages, **kwargs)
        data = extract_json(text)
        if not isinstance(data, dict):
            raise LLMError(f"期望模型返回 JSON 对象，实际是 {type(data).__name__}")
        return data
