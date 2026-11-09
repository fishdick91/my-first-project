"""Prompt 模板集中管理。

把硬编码在代码里的 prompt 抽到这里，方便维护和迭代。
动态部分用 {变量名} 占位，调用时用 .format() 注入。
"""

from .planner import PLANNER_SYSTEM_PROMPT
from .web_extract import WEB_EXTRACT_PROMPT, WEB_EXTRACT_FEEDBACK_PROMPT

__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "WEB_EXTRACT_PROMPT",
    "WEB_EXTRACT_FEEDBACK_PROMPT",
]
