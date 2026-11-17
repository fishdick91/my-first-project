# 网页提取链路性能与确定性优化

> 分支：`perf/web-extraction`　基线：`6eedf30`（`feature/scrapling-optimization`）
> 日期：2026-11-12

## 一、为什么做这三件事

上一分支只验证了静态站 `quotes.toscrape.com`（HTTP 快速路径 2.02 秒），
而项目主链路「知乎搜索」在日志里跑过 4 次，耗时 42 / 44 / 72 / 44 秒。
按日志反推，耗时大头不在抓取库，而在两处：

- `render_page` **无条件滚动 15 次**，每次 `800ms` + 最多 3s `networkidle`；
- `extract_data_with_ai` **最多 3 轮质量重提**，每轮重发最多 12000 字符。

另外，同一句「知乎搜索 失物招领，用表格展示标题和链接」四次跑出
`文章标题` / `标题` / `名称` 三套列名，导致导出表头不稳定、每次都触发
`VizStage` 的「列不存在，回退」告警，结果也无法对比或缓存。

本分支处理其中三项高性价比问题：自适应滚动、列名确定性、真实链路验证。
**没有动 LLM 重提轮数与提示词重试策略**（见「已知限制」）。

## 二、改动

| 文件 | 改动 |
|---|---|
| `sources/web_scraper.py` | `render_page` 增加 `idle_rounds` / `log`；每次滚动后比对「页面高度 + 候选记录块数量」，连续 2 次不变即停，`scroll_times` 变成上限；等待从 `800ms/3s` 降到 `400ms/2s`。`scrape_url` 增加抓取 / AI 提取分段耗时日志 |
| `sources/scrape_backend.py` | `fetch_html` 记录 HTTP 探测与 Playwright 渲染耗时，日志直接可见走了哪条路径 |
| `sources/column_names.py`（新增） | 同义词表把自由命名收敛到规范字段；目标名优先采用用户原话；固定列顺序；撞名加后缀不丢数据。只作用于 web 提取结果，不干扰固定源的展示别名 |
| `prompts/web_extract.py` | 增加规范名清单（标题 / 链接 / 评分 / …），减少模型每次换词 |
| `ai/planner.py` | 新增 `is_dynamic_fields_source`；对 web / `fetcher=web` 的源，代码兜底剥掉计划里预设的 `table.columns`，运行时展示全部真实列，不再每次回退告警 |
| `ai/llm.py` | 真实验证发现的 bug：模型偶发返回多段 JSON 时 `extract_json` 抛 `Extra data` 让整条管道崩掉。新增 `_parse_json_stream` 用 `raw_decode` 逐段解析并合并 |
| `tests/test_perf_web_extraction.py`（新增） | 20 例离线用例：列名收敛 / 用户原话 / 撞名 / 计划剥离 / 自适应滚动 / 分段日志 / 多段 JSON |

## 三、验证

| 检查 | 结果 |
|---|---|
| `compileall` | 通过 |
| `unittest discover -s tests` | **51/51 OK**（原 31 + 新增 20） |
| Streamlit `AppTest` | 无渲染异常 |
| 真实联网端到端：知乎搜索失物招领 | **30.7 秒**，4 行 `['标题','链接']`，`viz_warnings = None`，报告 2.7 KB |

真实链路日志（`logs/perf_web_e2e2_20261112_*.log`）首次给出分段耗时：

```
⚡ 快速 HTTP 内容不足（0.3s），降级 Playwright 渲染
🌐 Playwright 滚动 14/15 次（页面已稳定，提前结束）
⏱ Playwright 渲染耗时 13.4s
⏱ 抓取阶段 13.7s（501934 字符）
⏱ AI 提取阶段 11.3s（4 行）
```

对照优化前 4 次 42 / 44 / 72 / 44 秒：本次 30.7 秒，且计划里
`charts: [{"type":"table"}]` 不再带猜出来的 `columns`，全程无回退告警。

## 四、诚实结论

- **列名确定性达成**：`文章标题 / 名称 / 标题` 三种自由命名都收敛为
  `['标题','链接']`，顺序也一致；用户说「电影名」时保留「电影名」。
  这是本分支最实在的收益。
- **自适应滚动对知乎收益有限**：页面持续加载，实测滚了 14/15 次才停。
  收益主要体现在静态页 / 短列表（离线用例覆盖 3 轮即停）。
  知乎 13.4 秒的渲染成本基本还在。
- **30.7 秒不能全算作优化收益**：这次 LLM 只跑 1 轮就达标（4 行），
  而历史 72 秒那次很可能跑了 3 轮重提。分段耗时日志加上之后，
  以后可以准确对比，不必再靠总耗时反推。
- **HTTP 快速路径对知乎确实不命中**（0.3s 判定内容不足后正确降级），
  符合预期，不是回归。

## 五、已知限制 / 下一步

- `extract_data_with_ai` 仍是 3 轮重提、每轮重发完整清洗内容，且
  `WEB_EXTRACT_FEEDBACK_PROMPT` 不带上一轮结果（模型等于重新盲抽）；
  `_evaluate_extraction` 只要 ≥2 行就判合格，本次只提取 4 行也算通过。
  这是下一个最值得优化的点。
- `content_is_sufficient` 对「没有重复记录块的详情页」仍会误判为不足并降级；
  登录墙 / 验证码页也缺少识别。
- `http_get` 里 `Fetcher.get(..., impersonate="chrome")` 在
  `scrapling 0.5.1` 基础包下是死代码（缺 `curl_cffi`），当前实际走 `requests`；
  `sources/base.py` 已有的 `request_with_retry` 尚未复用。
- 反爬能力未增强，StealthyFetcher / Camoufox 仍建议攒够真实失败案例再评估。
