"""网页提取结果的列名归一化。

web 源的列名是 LLM 现场起的：同一句「知乎搜索失物招领，用表格展示标题和链接」，
四次运行得到过「文章标题 / 标题 / 名称」三种写法，导致导出列名不稳定、
每次都触发 VizStage 的列名回退告警，结果也没法对比或缓存。

这里不靠提示词求模型自觉，而是在数据离开爬虫前做一次确定性收敛：

1. 同义词表把自由命名映射到规范字段（标题 / 链接 / 评分 …）；
2. 目标名优先采用用户原话 —— 用户说「电影名」就保留「电影名」，
   说「标题」就输出「标题」，不会把用户的说法改成机器术语；
3. 列顺序按固定优先级排列，因此无论模型这次先给「链接」还是先给「名称」，
   最终 DataFrame 的列顺序都一致；
4. 两个原列映射到同一名字时加 `_2` 后缀，绝不丢数据。

只对 web 提取结果生效，固定字段源（豆瓣 / B 站 / CSV）不走这里，
所以不会干扰 `stages/rename.py` 的展示别名义逻辑。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# 顺序即「用户明确要求的字段」之外的默认展示优先级。
# 每组第一个元素是该字段的规范名。
CANONICAL_FIELDS: Dict[str, Tuple[str, ...]] = {
    "标题": (
        "标题", "文章标题", "名称", "作品名", "电影名", "片名", "书名", "歌曲名",
        "问题", "问题标题", "主题", "名字", "标题名称", "标题内容",
        "title", "name", "article_title", "question",
    ),
    "链接": (
        "链接", "网址", "地址", "详情页", "详情链接", "原文链接", "链接地址",
        "跳转地址", "url", "link", "href", "short_link", "web_url",
    ),
    "评分": ("评分", "分数", "得分", "打分", "星级", "rating", "score"),
    "导演": ("导演", "导演名", "director"),
    "作者": (
        "作者", "发布者", "发布人", "用户", "用户名", "答主", "up主", "博主",
        "发言人", "author", "user",
    ),
    "摘要": (
        "摘要", "简介", "描述", "内容", "正文", "简介内容", "概要",
        "desc", "description", "summary", "content",
    ),
    "时间": (
        "时间", "日期", "发布时间", "创建时间", "更新时间", "发表时间",
        "date", "time", "created_time", "publish_time",
    ),
    "热度": (
        "热度", "播放量", "浏览量", "阅读量", "点赞数", "评论数", "收藏数",
        "观看数", "views", "likes", "hot", "play_count",
    ),
}

def _norm(key: Any) -> str:
    """忽略大小写、空白、下划线、连字符与括号注释，便于同义词匹配。"""
    text = str(key).strip().lower()
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    return re.sub(r"[\s_\-·.]", "", text)


_ALIAS_TO_CANONICAL: Dict[str, str] = {
    _norm(alias): canonical
    for canonical, aliases in CANONICAL_FIELDS.items()
    for alias in aliases
}


def _target_names(instruction: Optional[str]) -> Dict[str, str]:
    """从用户原话里找出每个规范字段该用什么名字。

    例如「要电影名、评分、导演」→ {"标题": "电影名", "评分": "评分", "导演": "导演"}，
    「展示标题和链接」→ {"标题": "标题", "链接": "链接"}。
    同一字段命中多个说法时取最长的，避免「标题」压过「文章标题」。
    """
    if not instruction:
        return {}
    normalized = _norm(instruction)
    targets: Dict[str, str] = {}
    for canonical, aliases in CANONICAL_FIELDS.items():
        hits = [alias for alias in aliases if _norm(alias) and _norm(alias) in normalized]
        if hits:
            targets[canonical] = max(hits, key=lambda alias: len(_norm(alias)))
    return targets


def normalize_extracted_columns(
    records: List[Dict[str, Any]],
    instruction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """把 LLM 自由命名的列收敛成稳定列名，值原样保留。

    不增删字段：未知列保留原名，映射撞名时加后缀。返回的每行键顺序一致，
    因此 `pd.DataFrame(...)` 的列顺序也稳定。
    """
    if not records:
        return []

    columns: List[Any] = []
    seen = set()
    for row in records:
        if not isinstance(row, dict):
            return records  # 不是 dict 列表时原样返回，交给调用方处理
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)

    targets = _target_names(instruction)
    priority = {name: i for i, name in enumerate(CANONICAL_FIELDS)}

    def sort_key(col: Any):
        canonical = _ALIAS_TO_CANONICAL.get(_norm(col))
        if canonical is None:
            return (2, str(col))
        if canonical in targets:
            return (0, priority[canonical])
        return (1, priority[canonical])

    columns.sort(key=sort_key)

    mapping: Dict[Any, str] = {}
    used = set()
    for col in columns:
        canonical = _ALIAS_TO_CANONICAL.get(_norm(col))
        base = targets.get(canonical, canonical) if canonical else str(col)
        name = base
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        mapping[col] = name

    return [
        {mapping[col]: row[col] for col in columns if col in row}
        for row in records
    ]
