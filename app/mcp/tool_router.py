"""Semantic Tool Routing（阶段 1）：按关键词语义从工具集中选出 top-k 候选。

v1 为轻量本地方案：零依赖、零网络、无 embedding —— 直接用关键词命中
（英文/数字按 `[a-zA-Z0-9_]+` 切分，中文按 2-gram 切分）对 ToolInfo 的
name / description / input_schema 三字段加权打分。

设计要点：
- `Scorer` Protocol 预留扩展点，未来可换 embedding 实现（v1 不实现）。
- 工具总数 ≤ min_tools 时原样返回全部（小工具集零过滤，行为不变）。
- 查询无命中时返回原列表前 top_k（保底，不空手）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from app.mcp.schemas import ToolInfo

# 英文/数字/下划线 token：`[a-zA-Z0-9_]+` 小写切分（规格见规划文档阶段 1）
_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
# CJK 字符区间（中文为主，兼容日韩），用于 2-gram 切分
_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7AF),
)

# 字段权重：name 命中 ×3、description ×2、schema ×1（规划文档阶段 1 打分规则）
FIELD_WEIGHTS = {"name": 3.0, "description": 2.0, "schema": 1.0}


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """把文本切成查询/索引 token（小写，去重保序）。

    - 英文/数字按 `[a-zA-Z0-9_]+` 小写切分；同时把下划线分隔段作为补充 token
      （如 `demo_sql__ask` → demo / sql / ask），保证英文关键词也能命中工具名。
    - 中文按 2-gram 切分（「销售额」→「销售」「售额」）；孤立单字（标点隔开的
      单个 CJK 字符）作为单字 token 保留。
    - 无停用词表、无 jieba、无 embedding。
    """
    tokens: list[str] = []
    seen: set[str] = set()
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if _ASCII_TOKEN_RE.match(char):
            match = _ASCII_TOKEN_RE.match(text, i)
            assert match is not None
            run = match.group(0).lower()
            for part in run.split("_"):
                if part and part not in seen:
                    seen.add(part)
                    tokens.append(part)
            if run not in seen:
                seen.add(run)
                tokens.append(run)
            i = match.end()
        elif _is_cjk(char):
            # 连续 CJK 段按 2-gram 切分
            j = i
            while j < length and _is_cjk(text[j]):
                j += 1
            run = text[i:j]
            if len(run) == 1:
                if run not in seen:
                    seen.add(run)
                    tokens.append(run)
            else:
                for k in range(len(run) - 1):
                    gram = run[k : k + 2]
                    if gram not in seen:
                        seen.add(gram)
                        tokens.append(gram)
            i = j
        else:
            i += 1
    return tokens


@dataclass
class ToolDocument:
    """一个工具的检索索引：原始 ToolInfo + 拼接文本 + 分字段 token。"""

    tool: ToolInfo
    searchable_text: str
    field_tokens: dict[str, set[str]]


def _schema_searchable_text(input_schema: dict) -> str:
    """从 JSON Schema 提取可检索文本：参数名 + 参数描述。"""
    parts: list[str] = []
    for prop_name, prop_def in (input_schema.get("properties") or {}).items():
        parts.append(str(prop_name))
        if isinstance(prop_def, dict) and prop_def.get("description"):
            parts.append(str(prop_def["description"]))
    return " ".join(parts)


def build_documents(tools: list[ToolInfo]) -> list[ToolDocument]:
    """为工具列表建索引：name + description + schema 三字段分开存 token。"""
    documents: list[ToolDocument] = []
    for tool in tools:
        schema_text = _schema_searchable_text(tool.input_schema)
        searchable_text = " ".join([tool.name, tool.description, schema_text])
        documents.append(
            ToolDocument(
                tool=tool,
                searchable_text=searchable_text,
                field_tokens={
                    "name": set(tokenize(tool.name)),
                    "description": set(tokenize(tool.description)),
                    "schema": set(tokenize(schema_text)),
                },
            )
        )
    return documents


class Scorer(Protocol):
    """打分器协议：未来可换 embedding 等实现（v1 只有 KeywordScorer）。"""

    def score(self, query_tokens: list[str], doc: ToolDocument) -> float: ...


class KeywordScorer:
    """关键词加权打分：name ×3、description ×2、schema ×1，再乘命中覆盖率。"""

    def score(self, query_tokens: list[str], doc: ToolDocument) -> float:
        query_set = set(query_tokens)
        if not query_set:
            return 0.0
        name_hits = query_set & doc.field_tokens["name"]
        description_hits = query_set & doc.field_tokens["description"]
        schema_hits = query_set & doc.field_tokens["schema"]
        raw = (
            FIELD_WEIGHTS["name"] * len(name_hits)
            + FIELD_WEIGHTS["description"] * len(description_hits)
            + FIELD_WEIGHTS["schema"] * len(schema_hits)
        )
        if raw == 0:
            return 0.0
        all_hits = name_hits | description_hits | schema_hits
        coverage = len(all_hits) / len(query_set)
        return raw * coverage


class ToolRouter:
    """工具路由：查询 → 候选工具 top-k。

    - 工具总数 ≤ min_tools 时原样返回全部（保证小工具集零过滤）。
    - 命中为空时返回原列表前 top_k（保底，不空手）。
    """

    def __init__(
        self, top_k: int = 10, min_tools: int = 10, *, scorer: Scorer | None = None
    ) -> None:
        self.top_k = top_k
        self.min_tools = min_tools
        self.scorer = scorer or KeywordScorer()

    def search(self, query: str, tools: list[ToolInfo], top_k: int | None = None) -> list[ToolInfo]:
        k = top_k if top_k is not None else self.top_k
        if k <= 0:
            return []
        if len(tools) <= self.min_tools:
            return list(tools)
        query_tokens = tokenize(query)
        if not query_tokens:
            return list(tools)[:k]
        documents = build_documents(tools)
        scored: list[tuple[float, int, ToolDocument]] = []
        for index, doc in enumerate(documents):
            score = self.scorer.score(query_tokens, doc)
            if score > 0:
                scored.append((score, index, doc))
        if not scored:
            return list(tools)[:k]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [doc.tool for _, _, doc in scored[:k]]
