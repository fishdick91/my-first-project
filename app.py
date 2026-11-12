"""Streamlit 交互界面：一句话 → AI 管道 → 图表。

启动：
    streamlit run app.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime
from typing import Any, Dict, List, Optional

# 把 src 目录加入路径，让 pipeline_ai 包能被导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd
import streamlit as st

from pipeline_ai.ai import Planner, build_pipeline
from pipeline_ai.ai.source_generator import generate_source_config, save_source_config
from pipeline_ai.core import Context
from pipeline_ai.export import EXPORT_FORMATS, export_dataframe
from pipeline_ai.sources import cookie_store, registry
from pipeline_ai.stages import ProfileStage

st.set_page_config(page_title="AI 数据管道", layout="wide", page_icon="📊")


def _auto_register_site(site_id: str, url: str, use_login: bool = False) -> tuple[bool, str]:
    """自动注册一个站点为数据源。

    先尝试 JSON API 探测，失败则注册为 web 源（HTML 页面，AI 自动分析）。
    自动推断常见网站的搜索 URL。
    返回 (是否成功, 提示信息)。
    """
    import re as _re
    from pipeline_ai.ai.source_generator import detect_fields
    from pipeline_ai.sources.web_scraper import render_page

    # 常见网站的搜索 URL 模板（自动推断）
    SEARCH_URL_TEMPLATES = {
        "zhihu.com": "https://www.zhihu.com/search?type=content&q={keyword}",
        "weibo.com": "https://s.weibo.com/weibo?q={keyword}",
        "baidu.com": "https://www.baidu.com/s?wd={keyword}",
        "bilibili.com": "https://search.bilibili.com/all?keyword={keyword}",
        "douban.com": "https://www.douban.com/search?q={keyword}",
        "toutiao.com": "https://www.toutiao.com/search/?keyword={keyword}",
    }

    label = site_id
    try:
        html = render_page(url, site=site_id if use_login else None)
        title_match = _re.search(r"<title>(.*?)</title>", html, _re.IGNORECASE)
        if title_match:
            label = title_match.group(1).strip()[:30]
    except Exception:
        pass

    # 推断搜索 URL
    search_url = None
    for domain, tpl in SEARCH_URL_TEMPLATES.items():
        if domain in url:
            search_url = tpl
            break

    # 尝试 JSON 探测
    try:
        detected = detect_fields(url)
        cfg = {
            "id": site_id,
            "label": label,
            "fetcher": "http_json",
            "url": url,
            "params": {},
            "data_path": detected["data_path"],
            "fields": {},
            "headers": {},
            "params_overridable": {
                "limit": {"target": "__limit__", "default": 50, "min": 1, "max": 500}
            },
        }
        save_source_config(site_id, cfg)
        registry.reload()
        return True, f"已注册数据源「{label}」（JSON API，{len(detected['fields'])} 个字段）"
    except Exception:
        # JSON 探测失败，注册为 web 源（HTML 页面）
        cfg = {
            "id": site_id,
            "label": label,
            "fetcher": "web",
            "url": url,
            "fields": {},
        }
        if search_url:
            cfg["search_url"] = search_url
        save_source_config(site_id, cfg)
        registry.reload()
        search_note = "，支持关键词搜索" if search_url else ""
        return True, f"已注册数据源「{label}」（网页自动分析{search_note}）"


# ============================ 侧边栏：登录管理（零知识，用户不用碰 Cookie） ============================
with st.sidebar:
    st.markdown("### 🔑 登录管理")
    st.caption("需要登录的网站点这里，会弹出浏览器让你正常登录")

    login_site = st.selectbox(
        "选择网站",
        options=list(cookie_store.KNOWN_SITES.keys()) + ["custom"],
        format_func=lambda x: cookie_store.KNOWN_SITES.get(x, "🌐 自定义网站"),
        key="login_site",
    )

    # 自定义网站：只输入 URL，ID 自动从域名生成
    custom_url = ""
    if login_site == "custom":
        custom_url = st.text_input("网站 URL", placeholder="https://www.zhihu.com/", key="custom_url")
        if custom_url:
            from urllib.parse import urlparse
            domain = urlparse(custom_url).hostname or "custom"
            # 取域名主体（去掉 www. 和后缀）
            parts = domain.replace("www.", "").split(".")
            login_site = parts[0] if parts else "custom"

    # 显示当前登录状态
    data = cookie_store.load(login_site)
    if data:
        updated = data.get("updated_at", "")
        st.success(f"✅ 已登录（{updated}）")
        if st.button("清除登录态", key=f"clear_{login_site}"):
            cookie_store.delete(login_site)
            st.rerun()
    else:
        st.warning("⚠️ 未登录")

    if login_site == "custom" and custom_url and login_site not in registry.list_sources():
        if st.button("🚀 直接注册（不需登录）", use_container_width=True, key="auto_register"):
            with st.spinner("正在分析页面结构并注册数据源…"):
                ok, msg = _auto_register_site(login_site, custom_url, use_login=False)
                if ok:
                    st.success(f"✅ {msg}，主页即可使用")
                    st.rerun()
                else:
                    st.error(f"注册失败：{msg}")

    if st.button("🌐 打开浏览器登录", type="primary", use_container_width=True, key=f"login_{login_site}"):
        st.info("正在打开浏览器，请在弹出的窗口里完成登录…")
        try:
            cmd = [sys.executable, "-m", "pipeline_ai.sources.browser_login", login_site]
            if custom_url:
                cmd.append(custom_url)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=320,
                cwd=os.path.dirname(__file__),
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode == 0:
                st.success("✅ 登录成功，Cookie 已自动保存")
                # 登录成功后自动注册为数据源
                if custom_url and login_site not in registry.list_sources():
                    with st.spinner("正在分析页面结构并注册数据源…"):
                        try:
                            ok, msg = _auto_register_site(login_site, custom_url, use_login=True)
                            if ok:
                                st.success(f"✅ {msg}，主页即可使用")
                            else:
                                st.warning(f"⚠️ {msg}")
                        except Exception as exc:
                            st.warning(f"⚠️ 登录成功，但自动注册失败：{exc}")
            else:
                st.error(f"登录失败：{output or '未知错误'}")
        except subprocess.TimeoutExpired:
            st.error("登录超时（超过 5 分钟），请重试")
        except Exception as exc:
            st.error(f"启动登录失败：{exc}")
        st.rerun()

    st.divider()
    st.caption("💡 登录一次即可，后续爬取自动使用登录态")

    # 列出已有数据源
    st.divider()
    st.caption(f"📋 已注册数据源（{len(registry.list_sources())} 个）")
    for s in registry.list_sources():
        st.text(f"• {registry.get_label(s)} ({s})")

DEFAULT_ASK = "按播放量画 Top10 柱状图"

# ============================ 数据源展示标签（动态从注册表读取） ============================
def get_source_labels() -> Dict[str, str]:
    labels = {"csv": "📄 本地 CSV 文件"}
    for s in registry.list_sources():
        labels[s] = f"🔗 {registry.get_label(s)}"
    return labels

# 呈现形式 → 给 AI 的关键词
VIS_FORMATS = {
    "table": "表格",
    "bar": "柱状图",
    "line": "折线图",
    "scatter": "散点图",
}


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

    if csv_path:
        plan["source"] = {"type": "csv", "params": {"path": csv_path}}

    # 把用户原始需求存到 plan 顶层，供列名映射等环节使用
    plan["instruction"] = instruction

    # 网页数据源（web 源或 registry 中 fetcher=web 的源）把 instruction 传给 AI 提取
    src_type = plan.get("source", {}).get("type", "")
    if src_type == "web":
        plan.setdefault("source", {}).setdefault("params", {})
        plan["source"]["params"]["instruction"] = instruction
    else:
        try:
            from pipeline_ai.sources.registry import get_source_config
            cfg = get_source_config(src_type)
            if cfg and cfg.get("fetcher") == "web":
                plan.setdefault("source", {}).setdefault("params", {})
                plan["source"]["params"]["instruction"] = instruction
        except Exception:
            pass

    ctx = build_pipeline(plan, name="streamlit_app", log_dir="logs", profile=profile).run()
    return plan, ctx


def fmt_size(nbytes: int) -> str:
    if nbytes < 1024:
        return f"{nbytes} B"
    if nbytes < 1024 * 1024:
        return f"{nbytes / 1024:.1f} KB"
    return f"{nbytes / 1024 / 1024:.1f} MB"


def list_recent_charts(limit: int = 5) -> List[str]:
    """列出 output 目录下最近生成的 HTML 图表。"""
    out_dir = "output"
    if not os.path.isdir(out_dir):
        return []
    files = [
        os.path.join(out_dir, f)
        for f in os.listdir(out_dir)
        if f.endswith(".html")
    ]
    files.sort(key=os.path.getmtime, reverse=True)
    return files[:limit]


# ============================ 页面 ============================

st.title("📊 AI 数据管道")
st.caption("一句话描述需求，AI 自动识别数据源、生成管道、出图表")

# ============================ 一句话输入区 ============================
instruction = st.text_area(
    "描述你的需求",
    value=st.session_state.get("instruction", ""),
    height=120,
    placeholder=(
        "例如：\n"
        "爬豆瓣电影 Top50，展示电影名、评分、导演，按评分从高到低\n"
        "搜索 B 站 Python 教程，按播放量画 Top10 柱状图\n"
        "知乎搜索 失物招领，用表格展示标题和热度"
    ),
    key="instruction_input",
    label_visibility="collapsed",
)
st.session_state["instruction"] = instruction

# ---------- 可选：本地 CSV（折叠区） ----------
csv_path = None
with st.expander("📄 使用本地 CSV 文件（可选）", expanded=False):
    uploaded = st.file_uploader("上传 CSV 文件", type=["csv"], key="csv_upload")
    if uploaded is not None:
        csv_path = os.path.join(tempfile.gettempdir(), "streamlit_upload.csv")
        with open(csv_path, "wb") as f:
            f.write(uploaded.getbuffer())
        st.caption(f"已加载：{uploaded.name}（{len(pd.read_csv(csv_path))} 行）")

btn = st.button("🚀 生成", type="primary", use_container_width=True)

# ---------- 执行 ----------
if btn:
    # 校验：有 CSV 时 instruction 可为空（让 AI 根据画像分析）；否则必须有输入
    if csv_path is None and not instruction.strip():
        st.error("请描述你的需求，或上传 CSV 文件")
    else:
        full_instruction = instruction.strip()
        progress = st.progress(0, text="AI 正在设计管道计划…")
        try:
            plan, ctx = run_pipeline(full_instruction, csv_path)
            progress.progress(100, text="✅ 完成")
            st.session_state["plan"] = plan
            st.session_state["ctx"] = ctx
            st.session_state["charts"] = ctx.metadata.get("charts")
            # 保存最终 DataFrame，供导出使用
            if ctx.df is not None:
                st.session_state["result_df"] = ctx.df.copy()
            else:
                st.session_state.pop("result_df", None)
            st.success("✅ 完成，下方查看结果")
        except Exception as exc:  # noqa: BLE001
            progress.empty()
            st.session_state.pop("plan", None)
            st.session_state.pop("ctx", None)
            st.session_state.pop("charts", None)
            err_msg = str(exc)
            if "限流" in err_msg or "请求错误" in err_msg or "code=-400" in err_msg:
                st.error(f"执行失败：B 站接口临时限流，等 30 秒再试。\n\n原始错误：{err_msg}")
            elif "PlanError" in err_msg or "不存在的列" in err_msg:
                st.error(f"AI 计划有问题：{err_msg}\n\n👉 换种说法或简化需求再试。")
            else:
                st.error(f"执行失败：{err_msg}")

# ---------- 展示区 ----------
plan: Optional[Dict] = st.session_state.get("plan")
charts: Optional[str] = st.session_state.get("charts")
ctx = st.session_state.get("ctx")

if plan is not None:
    st.markdown("---")
    st.markdown("### 📋 执行结果")

    src = plan.get("source", {})
    src_type = src.get("type", "?")
    src_params = src.get("params", {})
    src_label = get_source_labels().get(src_type, src_type)
    if src_type == "bilibili_search":
        src_desc = f"{src_label}：{src_params.get('keyword', '?')}"
    elif src_type == "csv":
        src_desc = f"{src_label}：{os.path.basename(src_params.get('path', '?'))}"
    else:
        src_desc = src_label

    # 三栏展示区，与输入区结构一一对应
    res1, res2, res3 = st.columns([1, 1, 1])

    # ---- 第一栏：数据源 ----
    with res1:
        st.markdown("**① 数据源**")
        with st.container(border=True):
            st.markdown(f"**{src_desc}**")
            if src_params:
                st.caption(f"`{json.dumps(src_params, ensure_ascii=False)}`")
        if ctx is not None:
            st.metric("数据行数", ctx.metadata.get("initial_rows", "—"))
        st.metric("环节数", len(plan.get("stages", [])))
        if ctx is not None:
            duration = (datetime.now() - ctx.started_at).total_seconds()
            st.metric("耗时", f"{duration:.2f}s")

    # ---- 第二栏：要求（AI 执行计划 + 日志）----
    with res2:
        st.markdown("**② AI 执行计划**")
        st.markdown(f"**标题**：{plan.get('title', '')}")
        if plan.get("reason"):
            st.caption(f"思路：{plan['reason']}")
        aliases = ctx.metadata.get("column_aliases") if ctx is not None else None
        if aliases:
            st.caption("字段别名：" + "，".join(f"{k} → {v}" for k, v in aliases.items()))

        for i, stage in enumerate(plan.get("stages", []), 1):
            kind = stage.get("type", "?")
            params = stage.get("params", {})
            kind_label = {
                "clean": "🧹 清洗",
                "transform": "🔄 转换",
                "reduce": "📊 聚合",
                "profile": "🔍 画像",
                "duckdb": "🦆 存 DuckDB",
                "mysql": "🐬 存 MySQL",
                "file_sink": "💾 存文件",
                "viz": "📈 出图",
            }.get(kind, kind)
            with st.container(border=True):
                st.markdown(f"**步骤 {i}**：{kind_label}")
                if params:
                    if kind == "reduce":
                        gb = params.get("group_by")
                        agg = params.get("agg", {})
                        st.caption(f"按 `{gb}` 聚合 → {json.dumps(agg, ensure_ascii=False)}")
                    elif kind == "viz":
                        for c in params.get("charts", []):
                            ctype = c.get("type", "?")
                            if ctype == "table":
                                cols = c.get("columns", "全部列")
                                st.caption(f"· table columns={cols}")
                            else:
                                st.caption(f"· {ctype} x={c.get('x')} y={c.get('y')}" +
                                           (f" top={c.get('top')}" if c.get("top") else ""))
                    else:
                        st.caption(f"`{json.dumps(params, ensure_ascii=False)}`")

        with st.expander("完整 JSON 计划", expanded=False):
            st.json(plan)
        with st.expander("执行日志", expanded=False):
            if ctx is not None:
                st.text(ctx.summary())

    # ---- 第三栏：可视化成果 ----
    with res3:
        st.markdown("**③ 可视化成果**")
        viz_kinds = (ctx.metadata.get("viz_kinds") if ctx is not None else None) or []
        for warning in (ctx.metadata.get("viz_warnings") if ctx is not None else None) or []:
            st.warning(warning)
        if charts and os.path.exists(charts):
            size = fmt_size(os.path.getsize(charts))
            st.button(
                f"🌐 在浏览器打开（{size}）",
                type="primary",
                use_container_width=True,
                on_click=lambda: webbrowser.open(os.path.abspath(charts)),
            )
            st.caption(f"文件：`{os.path.basename(charts)}`")
            # 只有真正含图表时才把 1 MB 报告塞进 iframe；纯表格直接在下方看数据
            if any(kind != "table" for kind in viz_kinds):
                height = min(240 + 460 * len(viz_kinds), 1400)
                with open(charts, "r", encoding="utf-8") as f:
                    st.components.v1.html(f.read(), height=height, scrolling=True)
            else:
                st.caption("纯表格报告，完整数据见下方「结果数据」区")
        else:
            st.info("本次计划没有生成图表")

        recent = list_recent_charts()
        if recent:
            st.markdown("**🕘 历史图表**")
            for f in recent:
                name = os.path.basename(f)
                mtime = pd.Timestamp(os.path.getmtime(f), unit="s").strftime("%H:%M:%S")
                st.button(
                    f"📄 {name}（{mtime}）",
                    use_container_width=True,
                    on_click=lambda p=f: webbrowser.open(os.path.abspath(p)),
                    key=f"hist_btn_{name}",
                )

    # ---- 结果数据 + 按需导出 ----
    result_df = st.session_state.get("result_df")
    if result_df is not None and not result_df.empty:
        st.markdown("---")
        st.markdown("### 📄 结果数据")
        st.caption(
            f"共 {len(result_df)} 行 × {len(result_df.columns)} 列；"
            "表格可排序，完整图表报告在上方打开"
        )
        st.dataframe(result_df, use_container_width=True, hide_index=True, height=420)

        st.markdown("**💾 导出**（选择格式后只生成该格式文件）")
        export_title = plan.get("title", "数据导出")
        safe_title = "".join(
            c if c not in '\\/:*?"<>|' else "_" for c in str(export_title)
        )
        pick, download = st.columns([1, 2])
        with pick:
            fmt = st.selectbox(
                "导出格式",
                options=list(EXPORT_FORMATS),
                format_func=lambda k: EXPORT_FORMATS[k]["label"],
                label_visibility="collapsed",
                key="export_format",
            )
        with download:
            cfg = EXPORT_FORMATS[fmt]
            try:
                # 只构造选中的格式，不再每次重跑都生成 Word/PDF 等 5 份文件
                data = export_dataframe(result_df, fmt, title=export_title)
                st.download_button(
                    label=f"📥 下载 {cfg['label']}",
                    data=data,
                    file_name=f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{cfg['ext']}",
                    mime=cfg["mime"],
                    use_container_width=True,
                    key="export_download",
                )
            except Exception as exc:
                st.button(f"📥 {cfg['label']} 不可用", disabled=True, use_container_width=True)
                st.caption(f"导出失败：{exc}")
else:
    st.info("👆 在上方输入需求，点「生成」开始")
