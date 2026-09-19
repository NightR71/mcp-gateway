"""只读简历知识库 MCP Server（M3）：语义检索知识卡片供简历 Agent 使用。

独立运行方式（与 demo_sql_server 一致，供 stdio/HTTP 复用）：
    python servers/resume_kb_server/server.py            # stdio
    RESUME_KB_TRANSPORT=http python .../server.py        # Streamable HTTP
    RESUME_KB_TRANSPORT=sse python .../server.py         # SSE

环境变量：
    RESUME_KB_PATH       知识库根目录（默认项目根的 knowledge/）
    RESUME_KB_TRANSPORT  stdio（默认）/ http / sse
    RESUME_KB_HOST       http/sse 监听地址（默认 0.0.0.0）
    RESUME_KB_PORT       http/sse 端口（默认 9002）

工具描述按网关语义路由的分词规则书写（中文 2-gram、英文数字小写切分），
描述里的「简历 / 项目 / 经历 / 技术 / 面试 / 检索」等词用于让网关的
`GET /tools?query=` 与 Agent 注入侧能召回本 Server 的工具。

只读保证：全部工具均为查询语义，无写入/删除/执行类工具。
文件同时支持脚本方式运行（sys.path[0] 为本目录）与包导入（网关 inprocess）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

try:  # 包导入（网关进程内加载）
    from .kb import KnowledgeBase, KnowledgeCard
except ImportError:  # 脚本方式运行（stdio 子进程 / docker）
    from kb import KnowledgeBase, KnowledgeCard

DEFAULT_MAX_TOP_K = 5

server = MCPServer("resume_kb_server")

_kb: KnowledgeBase | None = None


def _default_root() -> Path:
    """知识库默认根目录：项目根的 knowledge/（本文件位于 servers/resume_kb_server/）。"""
    return Path(__file__).resolve().parents[2] / "knowledge"


def get_kb() -> KnowledgeBase:
    """惰性构造进程级知识库实例（路径取 RESUME_KB_PATH，缺省项目根 knowledge/）。"""
    global _kb
    if _kb is None:
        _kb = KnowledgeBase(os.getenv("RESUME_KB_PATH") or _default_root())
    return _kb


def _clamp_top_k(value: Any, default: int = 3) -> int:
    """入参设界：top_k 收敛到 1..DEFAULT_MAX_TOP_K（越界不报错，静默收敛）。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(DEFAULT_MAX_TOP_K, number))


def _card_digest(card: KnowledgeCard) -> str:
    """卡片摘要行（检索结果用，不返回全文）。

    标签用 `display_tags`（可展示别名）而非 `tags`（召回别名）——后者含雇主实名，
    一经下发即进入模型上下文（T4 裁决：别名只用于命中，回答一律行业描述）。
    """
    tags = "、".join(card.display_tags[:8])
    return f"- `{card.id}`（{card.type}）{card.title}\n  摘要：{card.summary}\n  标签：{tags}"


def _card_full(card: KnowledgeCard) -> str:
    """完整卡片文本（含 frontmatter 关键字段 + 正文），供模型据此作答。"""
    header = [f"# {card.title}", f"- id：{card.id}", f"- 类别：{card.type}"]
    tags = card.display_tags
    if tags:
        header.append(f"- 标签：{'、'.join(tags)}")
    return "\n".join(header) + "\n\n" + card.body


@server.tool()
async def search_knowledge(query: str, top_k: int = 3) -> str:
    """按语义检索候选人的简历知识库，返回最相关的知识卡片摘要。

    知识库覆盖：项目经历（MCP 网关 / 桌面智能体 / 鸿蒙智能家居 / 医疗微服务 /
    海洋数据 NL2SQL）、技术问答（MCP / RAG / LoRA / asyncio / SSE / 数据库 /
    限流 / 批量导入）、基本信息与自我介绍、优势主线故事与岗位匹配。
    面试提问、项目追问、技术问题都应先用本工具检索，再据检索结果作答。
    """
    kb = get_kb()
    k = _clamp_top_k(top_k)
    cards = kb.search(query, top_k=k)
    if not cards:
        return "知识库为空或未找到匹配条目。可先用 list_cards 查看全部可用主题。"

    lines = [f"检索「{query}」命中 {len(cards)} 张知识卡片："]
    lines.extend(_card_digest(card) for card in cards)
    lines.append("\n如需完整内容，请用 get_card 传入卡片 id。")
    return "\n".join(lines)


@server.tool()
async def get_card(card_id: str) -> str:
    """按卡片 id 读取某个项目或主题的完整知识卡片内容（项目细节、量化数字、
    技术方案、追问预案、边界约束）。search_knowledge 命中后用它取全文。
    """
    card = get_kb().get(card_id)
    if card is None:
        available = "、".join(card.id for card in get_kb().cards) or "（空）"
        return f"未找到卡片 {card_id!r}。可用卡片：{available}"
    return _card_full(card)


@server.tool()
async def list_cards() -> str:
    """列出知识库全部知识卡片的 id、类别与标题（按类别分组）。

    用于了解候选人有哪些可介绍的项目与主题（类别：profile 基本信息 /
    project 项目档案 / qa 技术问答 / evidence 优势证据）。
    """
    kb = get_kb()
    cards = kb.cards
    if not cards:
        return "知识库为空。"
    lines = [f"知识库共 {len(cards)} 张卡片："]
    for card_type in kb.types():
        lines.append(f"\n【{card_type}】")
        lines.extend(f"- `{card.id}`：{card.title}" for card in cards if card.type == card_type)
    return "\n".join(lines)


@server.tool()
async def get_profile() -> str:
    """读取候选人的基本信息与自我介绍（姓名、学校专业、毕业年份、求职方向、
    经历概览、30 秒自我介绍口径、脱敏约定与成绩口径）。

    回答「介绍一下你自己」「你叫什么」「哪个学校什么专业」「联系方式」
    这类基本问题时使用。
    """
    card = get_kb().get("profile-basic")
    if card is None:
        return "基本信息卡（profile-basic）缺失，请检查知识库目录。"
    return _card_full(card)


if __name__ == "__main__":
    transport = os.getenv("RESUME_KB_TRANSPORT", "stdio")
    if transport in ("http", "sse"):
        server.run(
            transport="streamable-http" if transport == "http" else "sse",
            host=os.getenv("RESUME_KB_HOST", "0.0.0.0"),
            port=int(os.getenv("RESUME_KB_PORT", "9002")),
        )
    else:
        server.run(transport="stdio")
