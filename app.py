"""Streamlit 交互界面：一句话 → AI 管道 → 图表。

启动：
    streamlit run app.py

界面：
    顶部：输入框 + 生成按钮 + 可选 CSV 上传
    左侧：AI 返回的 JSON 计划（可折叠）
    右侧：生成的图表 HTML
"""

from __future__ import annotations

import json
import os
import tempfile
import webbrowser
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

from pipeline_ai.ai import Planner, build_pipeline
from pipeline_ai.core import Context
from pipeline_ai.stages import ProfileStage

st.set_page_config(page_title="AI 数据管道", layout="wide", page_icon="📊")

DEFAULT_ASK = "抓取 B 站热门视频，按作者汇总播放量，画 Top10 柱状图"


def build_csv_profile(path: str) -> Dict[str, Any]:
    """本地 CSV 先做一次体检，把画像喂给模型。"""
    df = pd.read_csv(path)
    ctx = Context("csv_profile")
    ProfileStage()(df, ctx)
    return ctx.metadata["profile"]


def run_pipeline(instruction: str, csv_path: Optional[str] = None):
    """执行完整链路：出计划 → 跑管道 → 返回 (plan, ctx)。"""
    profile = None
    extra_context = None
    if csv_path:
        profile = build_csv_profile(csv_path)
        extra_context = (
            f'数据源固定为 csv，source.params.path 必须是 "{csv_path}"；'
            "请根据画像里的列设计清洗、聚合和图表环节。"
        )

    planner = Planner()
    plan = planner.plan(instruction, profile=profile, extra_context=extra_context)

    # CSV 模式下，强制把 source 改成这个 csv，避免模型没听话
    if csv_path:
        plan["source"] = {"type": "csv", "params": {"path": csv_path}}

    ctx = build_pipeline(plan, name="streamlit_app", log_dir="logs", profile=profile).run()
    return plan, ctx


# ============================ 页面 ============================

st.title("📊 AI 数据管道")
st.caption("用一句话描述需求，AI 自动生成清洗/聚合/可视化计划并执行")

# ---------- 顶部输入区 ----------
col_ask, col_csv = st.columns([3, 1])
with col_ask:
    instruction = st.text_input("你的需求", value=DEFAULT_ASK, key="ask")
with col_csv:
    uploaded = st.file_uploader("可选：上传 CSV", type=["csv"], key="csv")

btn = st.button("🚀 生成", type="primary", use_container_width=True)

# ---------- 执行 ----------
if btn:
    if not instruction.strip():
        st.error("需求不能为空")
    else:
        with st.spinner("AI 正在设计管道计划…"):
            try:
                csv_path = None
                if uploaded is not None:
                    # 存到临时文件，路径固定给管道用
                    tmp = os.path.join(tempfile.gettempdir(), "streamlit_upload.csv")
                    with open(tmp, "wb") as f:
                        f.write(uploaded.getbuffer())
                    csv_path = tmp

                plan, ctx = run_pipeline(instruction, csv_path)
                st.session_state["plan"] = plan
                st.session_state["ctx"] = ctx
                st.session_state["charts"] = ctx.metadata.get("charts")
                st.success("✅ 管道执行完成")
            except Exception as exc:  # noqa: BLE001
                st.session_state.pop("plan", None)
                st.session_state.pop("ctx", None)
                st.session_state.pop("charts", None)
                st.error(f"执行失败：{exc}")

# ---------- 展示区 ----------
plan: Optional[Dict] = st.session_state.get("plan")
charts: Optional[str] = st.session_state.get("charts")

if plan is not None:
    left, right = st.columns([1, 2])

    # 左侧：计划
    with left:
        st.subheader("AI 计划")
        st.markdown(f"**标题**：{plan.get('title', '')}")
        st.markdown(f"**思路**：{plan.get('reason', '')}")
        with st.expander("查看 JSON 计划", expanded=False):
            st.json(plan)
        with st.expander("执行日志", expanded=False):
            ctx = st.session_state.get("ctx")
            if ctx is not None:
                st.text(ctx.summary())

    # 右侧：图表（只给打开按钮，不在 UI 里重复显示，浏览器看更清晰）
    with right:
        st.subheader("图表")
        if charts and os.path.exists(charts):
            if st.button("🌐 在浏览器中打开图表", type="primary", use_container_width=True):
                webbrowser.open(os.path.abspath(charts))
            st.caption(f"文件：`{charts}`（点击上方按钮用浏览器打开，全屏交互体验更好）")
        else:
            st.info("本次计划没有生成图表")
else:
    st.info("👆 在上方输入需求，点「生成」开始")
