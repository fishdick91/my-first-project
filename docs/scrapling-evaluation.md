# Scrapling 调研与落地说明

> 分支：`feature/scrapling-optimization`　调研日期：2026-11-12
> 来源：<https://github.com/D4Vinci/Scrapling>　文档：<https://scrapling.readthedocs.io>
> 本地实测版本：`scrapling 0.5.1`（`pip install scrapling`，基础包**仅含 parser**）

## 一、从 Scrapling 里挑出来的东西

### 值得借鉴，已落地

**1. 按成本递增的分层抓取**

官方 *Choosing a Fetcher* 的核心思想：`Fetcher`（快速 HTTP）→ `StealthyFetcher` / 浏览器逐级升级，
"start light with HTTP and switch to a browser only when a site blocks you"。

本项目原来**无条件**用 Playwright：启动 Chromium、等 networkidle、滚动 15 次，
每次滚动 `800ms` + 最多 `3s` 的 networkidle 等待 —— 光滚动就是 12~57 秒，
静态页面也一样付这个代价。

→ `auto` 策略先发一次 HTTP；用「内容体检」（重复记录块数量 / 正文长度）判断够不够：
够就直接用，SPA 空壳、403、反爬拦截页自动降级到原 Playwright 渲染。

**2. 用解析器按 DOM 定位相似元素，而不是正则切整页**

借鉴 `Selector` / `find_similar` 的思路。原 `simplify_html` 的三个问题：

- `<li[\s\S]*?</li>` 是非贪婪正则，遇到嵌套列表会把记录截断；
- 超长时 `priority[:max_length]` 直接从字符中间切断，半条记录喂给 LLM；
- 截断后从 `text_only[-remaining:]` 取**尾部**补内容，前面的数据反而丢掉。

→ `html_to_llm_text` 自动发现重复容器（先常见选择器，再按 `(tag, class)` 分组），
输出「每行一条记录」的精简 HTML 片段：按记录边界截断、保留 `<a href>` 供提取链接、
去掉 script/style/nav/head/link 等噪声。

### 明确不引入

| Scrapling 能力 | 不引入的原因 |
|---|---|
| adaptive 选择器 / `auto_save` | 项目不做 CSS 选择器定位，整页内容交 LLM 提取，网站改版不会让「选择器」失效 |
| Spider / 并发爬取 / 暂停恢复 / ProxyRotator | 当前是「一句话 → 单页」，没有全站爬取需求 |
| `page.markdown()`（RAG-ready） | 需 `scrapling[rag]` extra；且纯 Markdown 会丢 `href`，「链接」字段会消失 |
| StealthyFetcher / Camoufox / Cloudflare 绕过 | 需 `scrapling[fetchers]` + `scrapling install` 下载浏览器二进制（数百 MB）。应攒够真实反爬失败案例再引入 |
| MCP server / Agent Skill / CLI | 项目已有自己的 LLM 编排链路，不需要第二套 agent 接口 |

### 版本事实（实测，不要照抄新版文档）

- 0.5.1 基础包可用：`Selector(content=html)`、`css()`、`get_all_text(ignore_tags=...)`、`html_content`
- 0.5.1 **没有** `.markdown()`（文档描述的是带 `rag` extra 的版本）
- `scrapling.fetchers` 依赖 `curl_cffi`，基础包导入必然 `ModuleNotFoundError`
  → 因此 `http_get` 用 try/except 自动回退到项目已有的 `requests`

## 二、分支改动

| 文件 | 改动 |
|---|---|
| `sources/scrape_backend.py`（新增） | 策略解析、`http_get`、`content_is_sufficient`、`fetch_html`、`html_to_llm_text`；Scrapling 全程可选，缺失自动回退 |
| `sources/web_scraper.py` | `scrape_url` 增加 `strategy` / `log` 参数并走 `fetch_html`；`extract_data_with_ai` 改用结构化清洗；原 `render_page` / `simplify_html` 保留为兜底 |
| `sources/web_source.py` | 透传 `strategy` 与 `ctx.log`，执行日志可看到实际用了哪个后端 |
| `prompts/web_extract.py` | 提示词改为描述「已按记录边界清洗的内容」，明确链接取 `href` |
| `config/sources.yaml` | `zhihu` 增加 `scrape_strategy: auto` 示例（按站点可覆盖） |
| `pyproject.toml` | 新增可选 extras：`scrape`（基础 parser）、`scrape-full`（完整 fetchers），**不进主依赖** |
| `tests/test_scrape_backend.py`（新增） | 15 例离线用例：清洗、内容体检、策略切换、优先级、链路透传 |

## 三、启用与回退

```bash
# 可选依赖（不装也能跑，会自动回退到 requests + 正则清洗）
.venv/Scripts/python.exe -m pip install "scrapling>=0.5,<0.6"

# 策略优先级：显式参数 > 数据源 scrape_strategy > 环境变量 > 默认 auto
SCRAPE_STRATEGY=auto      # HTTP 优先，内容不足降级浏览器（默认）
SCRAPE_STRATEGY=fast      # 只用 HTTP，失败直接报错，便于测静态站点
SCRAPE_STRATEGY=browser   # 一键回退到旧的纯 Playwright 行为
```

也可在 `config/sources.yaml` 的单个 web 源上写 `scrape_strategy: browser`，
让已知必须渲染的站点跳过那次 HTTP 探测。

## 四、验证结果

| 检查 | 结果 |
|---|---|
| `compileall` | 通过 |
| `unittest discover -s tests -v` | **31/31 OK**（含原有 16 例） |
| Streamlit `AppTest` | 无异常 |
| 真实静态站点 `quotes.toscrape.com` | HTTP 快速路径 **2.02 秒**返回 11021 字符，内容体检判定充足 → 跳过浏览器（旧路径光滚动就 ≥12 秒） |
| 离线噪声页清洗 | 自动识别 `div.quote` 等重复容器，输出 10 行记录，保留 `href`，噪声标签清除 |

清洗后的输入体积与原实现大体相当（都在同一量级），**真正的收益是切分正确性与抓取耗时**：
不会再把半条记录喂给 LLM，静态页面不再为浏览器渲染买单。

## 五、风险与后续

- 默认 `auto` 对 SPA 站点会多一次 HTTP 探测（约 1~2 秒）再降级，可接受；不想要就设 `browser`。
- **LLM 提取耗时不在本次优化范围内**：`extract_data_with_ai` 仍是最多 3 轮质量评估重提，
  换抓取库省不掉这段（知乎实测 42~72 秒的大头仍可能在这里）。
- 反爬能力尚未增强：当前只有 UA + cookie，遇 Cloudflare 仍会失败。
  建议累计 3~5 个真实失败案例后，再评估 `scrapling[fetchers]` / StealthyFetcher。
- `douban_top` 双轨数据源、README 单行等历史欠账不在本分支范围。
