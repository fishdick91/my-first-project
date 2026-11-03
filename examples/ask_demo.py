"""一句话 → AI 生成管道 → 执行 → 出图。

用法：
    python examples/ask_demo.py "抓取B站热门视频，按作者汇总播放量，画 Top10 柱状图"
    python examples/ask_demo.py --csv data/sales.csv "按城市汇总销售额并画柱状图"
    python examples/ask_demo.py --dry-run "看看 AI 会怎么设计这条管道"

第一次跑之前，先在 .env 里填好（智谱 GLM-4-Flash 免费）：
    LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
    LLM_API_KEY=你的Key
    LLM_MODEL=glm-4-flash
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict

import pandas as pd

from pipeline_ai.ai import Planner, build_pipeline
from pipeline_ai.core import Context
from pipeline_ai.stages import ProfileStage

DEFAULT_ASK = "抓取 B 站热门视频，按播放量取前 10 名画柱状图，再看播放量和点赞数的关系"


def build_csv_profile(path: str) -> Dict[str, Any]:
    """本地 CSV 先做一次体检，把画像喂给模型，让它知道有哪些列。"""
    df = pd.read_csv(path)
    ctx = Context("csv_profile")
    ProfileStage()(df, ctx)
    return ctx.metadata["profile"]


def main() -> None:
    parser = argparse.ArgumentParser(description="用自然语言生成并执行数据管道")
    parser.add_argument("ask", nargs="*", help="自然语言需求")
    parser.add_argument("--csv", help="改用本地 CSV 作为数据源")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = parser.parse_args()

    instruction = " ".join(args.ask) or DEFAULT_ASK

    profile = None
    extra_context = None
    if args.csv:
        profile = build_csv_profile(args.csv)
        extra_context = (
            f'数据源固定为 csv，source.params.path 必须是 "{args.csv}"；'
            "请根据画像里的列设计清洗、聚合和图表环节。"
        )

    planner = Planner()
    plan = planner.plan(instruction, profile=profile, extra_context=extra_context)

    print("=" * 60)
    print(f"需求：{instruction}")
    print(f"标题：{plan.get('title')}")
    print(f"思路：{plan.get('reason')}")
    print("-" * 60)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print("=" * 60)

    if args.dry_run:
        print("--dry-run：只出计划，不执行")
        return

    ctx = build_pipeline(plan, name="ask_demo", log_dir="logs", profile=profile).run()

    print()
    print(ctx.summary())
    if ctx.metadata.get("charts"):
        print(f"\n打开这个文件看图表: {ctx.metadata['charts']}")


if __name__ == "__main__":
    main()
