"""知识库加载与语义检索（resume_kb_server 核心，只读）。

与 `app/` 保持「双向无依赖」：本模块不 import app 任何东西。分词与打分规则
与 `app/mcp/tool_router.py` 同源（中文 2-gram、英文/数字按 `[a-zA-Z0-9_]+`），
使知识卡片的召回行为与网关的语义工具路由一致；两侧规则由测试各自守护。

检索为关键词加权打分（零依赖、零网络、无 embedding）：
title ×3 / tags ×2 / 正文 ×1，再乘命中覆盖率；`type` 可作为过滤条件。
召回用 `tags`（含雇主实名等别名），**展示一律用 `display_tags`**（见 NON_DISPLAY_ALIASES）。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# 分词（与 app/mcp/tool_router.py 同规则：中文 2-gram + 英文数字小写切分）
# ---------------------------------------------------------------------------

_ASCII_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")
_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x3040, 0x30FF),
    (0xAC00, 0xD7AF),
)

# 字段权重：title ×3、tags ×2、正文 ×1
FIELD_WEIGHTS = {"title": 3.0, "tags": 2.0, "body": 1.0}

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# 不可展示的召回别名（T4 裁决延伸，2026-09-16）：雇主实名等别名**只参与召回打分**，
# 绝不进入交给模型/前端的文本——否则模型每次检索到这两张卡都会读到实名并可能照抄作答，
# 「回答一律行业描述」只剩提示词级约束。
# 为什么收口在这里：这是**知识内容**层面的策略（与 FIELD_WEIGHTS、分词规则同类），
# 过滤只由 `display_aliases` 一处实现，新加工具时不可能漏掉某个出口；且 kb.py 与 app/
# 双向无依赖，本 Server 以 stdio 子进程方式单独运行时同样生效。
# 为什么不塞进 output_guard（结论：不做）：那是 PII 打码器，按「保留前后位」等长打码，
# 实名打码后仍留可识别前缀（「中电**」）且把答案改成残句；更关键的是，别名在出口已被
# 结构性切断，唯一残余来源是面试官自己在提问里打出的公司名——对提问者隐藏他刚打的词
# 只会产出破碎文本并暴露守门。口径纠正的正解是第 2 层（人设规则 + company_boundary）。
NON_DISPLAY_ALIASES: tuple[str, ...] = ("中电福富", "海科新质", "海科")


def display_aliases(aliases: Iterable[str]) -> tuple[str, ...]:
    """过滤出可展示的别名（保序）：含任一实名的别名一律剔除（实名作子串也剔除）。"""
    return tuple(
        alias for alias in aliases if not any(banned in alias for banned in NON_DISPLAY_ALIASES)
    )


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(lo <= code <= hi for lo, hi in _CJK_RANGES)


def tokenize(text: str) -> list[str]:
    """把文本切成检索 token（小写、去重保序）。

    - 英文/数字按下划线分段切分（`resume_kb__search` → resume / kb / search）；
    - 中文按 2-gram 切分（「销售额」→「销售」「售额」），孤立单字保留为单 token。
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


# ---------------------------------------------------------------------------
# 卡片模型与加载
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """拆出 YAML frontmatter 与正文；无 frontmatter 时返回空元数据 + 全文。"""
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    data = yaml.safe_load(match.group(1)) or {}
    if not isinstance(data, dict):
        data = {}
    return data, text[match.end() :]


@dataclass
class KnowledgeCard:
    """一张知识卡片：元数据 + 正文 + 预计算的检索 token。"""

    id: str
    type: str
    title: str
    tags: tuple[str, ...]
    body: str
    source: str  # 相对知识库根的路径（展示/调试用）
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        """一句话摘要：正文里第一个非标题、非引用块的非空段落。"""
        for raw in self.body.splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", ">", "|", "-", "*", "```")):
                continue
            return line
        return self.title

    @property
    def display_tags(self) -> tuple[str, ...]:
        """可对外展示的标签：剔除雇主实名等不可展示别名。

        与 `tags` 的分工：`tags` 是**召回别名**（含实名，检索打分照用，T4 裁决），
        `display_tags` 是**可展示别名**（渲染给模型/前端的文本只准用它）。
        """
        return display_aliases(self.tags)

    def field_tokens(self) -> dict[str, set[str]]:
        return {
            "title": set(tokenize(self.title)),
            "tags": set(tokenize(" ".join(self.tags))),
            "body": set(tokenize(self.body)),
        }


class KnowledgeBase:
    """`knowledge/` 目录的只读视图（惰性加载一次，进程内缓存）。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cards: list[KnowledgeCard] = []
        self._by_id: dict[str, KnowledgeCard] = {}
        self._loaded = False

    # -- 加载 ---------------------------------------------------------------

    def load(self) -> None:
        """加载全部 `*.md`（跳过 README.md 等无 id 的说明文件）。"""
        if self._loaded:
            return
        cards: list[KnowledgeCard] = []
        if self.root.is_dir():
            for path in sorted(self.root.rglob("*.md")):
                if path.name == "README.md":
                    continue
                card = self._load_card(path)
                if card is not None:
                    cards.append(card)
        self._cards = cards
        self._by_id = {card.id: card for card in cards}
        self._loaded = True

    def _load_card(self, path: Path) -> KnowledgeCard | None:
        """单文件 → 卡片；缺 id 则跳过（说明性文档不是知识卡片）。"""
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        card_id = str(meta.get("id") or "").strip()
        if not card_id:
            return None
        tags = meta.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        return KnowledgeCard(
            id=card_id,
            type=str(meta.get("type") or "unknown"),
            title=str(meta.get("title") or card_id),
            tags=tuple(str(t) for t in tags),
            body=body.strip(),
            source=str(path.relative_to(self.root)).replace("\\", "/"),
            meta=meta,
        )

    # -- 查询 ---------------------------------------------------------------

    @property
    def cards(self) -> list[KnowledgeCard]:
        self.load()
        return list(self._cards)

    def get(self, card_id: str) -> KnowledgeCard | None:
        self.load()
        return self._by_id.get(card_id)

    def types(self) -> list[str]:
        """全部卡片类别（保序去重）。"""
        seen: list[str] = []
        for card in self.cards:
            if card.type not in seen:
                seen.append(card.type)
        return seen

    def search(
        self, query: str, top_k: int = 3, card_type: str | None = None
    ) -> list[KnowledgeCard]:
        """按查询检索卡片：加权关键词打分，返回得分最高的 top_k 张。

        - `card_type` 指定时只在同类卡片内检索（如只看 project）；
        - 查询无 token 或全部零分时退回「按类别顺序取前 top_k」（保底不空手）。
        """
        candidates = self.cards
        if card_type:
            candidates = [c for c in candidates if c.type == card_type]
        if not candidates or top_k <= 0:
            return []

        query_tokens = set(tokenize(query))
        if not query_tokens:
            return candidates[:top_k]

        scored: list[tuple[float, int, KnowledgeCard]] = []
        for index, card in enumerate(candidates):
            score = self._score(query_tokens, card)
            if score > 0:
                scored.append((score, index, card))
        if not scored:
            return candidates[:top_k]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [card for _, _, card in scored[:top_k]]

    @staticmethod
    def _score(query_tokens: set[str], card: KnowledgeCard) -> float:
        """加权打分：title ×3 + tags ×2 + body ×1，再乘命中覆盖率。"""
        fields = card.field_tokens()
        hits = {name: query_tokens & tokens for name, tokens in fields.items()}
        raw = sum(FIELD_WEIGHTS[name] * len(found) for name, found in hits.items())
        if raw == 0:
            return 0.0
        coverage = len(set().union(*hits.values())) / len(query_tokens)
        return raw * coverage
