"""评测集 65 问的零成本回归（把 `docs/M2-评测集-50问.md` 的人读底稿变成可机跑断言）。

背景：知识库卡片或人设话术的**任何一次改动**，都可能悄悄弄坏某一问的召回或口径。
在此之前这件事只能靠人工抽查（改一句话不知道会不会踩坏哪一问）；本文件把它变成
CI 每次 push 自动跑的三类断言，全部**零模型、零网络、零成本**：

1. `must_recall`         —— 检索 top-k 必须命中期望卡片（语义路由的准入门槛）；
2. `must_hit_policy`     —— 输入侧固定回应必须命中指定话术，即**不进模型**（确定性拦截）；
3. `must_not_intercept`  —— 必须**放行给模型**（红线词表不得误伤正常技术问答）。

数据集：`tests/fixtures/eval_questions.yaml`（随本文件入库；删改契约必须一起改）。
真模型侧（回答是否覆盖要点、是否零 PII）不在本文件范围——那需要 Key，由
`scripts/m5_smoke.py` 手动跑（见 `docs/M7-收尾报告.md` §6）。
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from app.agent.persona import ReplyPolicyMatcher
from app.config import ReplyPolicyConfig
from servers.resume_kb_server.kb import KnowledgeBase

BASE_DIR = Path(__file__).resolve().parents[1]
EVAL_SET_PATH = Path(__file__).parent / "fixtures" / "eval_questions.yaml"
KB_ROOT = BASE_DIR / "knowledge"
PRODUCTION_RESUME_KB = BASE_DIR / "config" / "resume_kb.yaml"

# 评测集底稿的固定规模：A6 + B13 + C8 + D8 + E7 + F4 + G8 + H11 = 65
EXPECTED_GROUP_COUNTS = {"A": 6, "B": 13, "C": 8, "D": 8, "E": 7, "F": 4, "G": 8, "H": 11}

PII_PATTERNS = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}"),
    "id_card": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
}
EMPLOYER_NAMES = ("中电福富", "海科")


@pytest.fixture(scope="module")
def eval_set() -> dict:
    return yaml.safe_load(EVAL_SET_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase(KB_ROOT)


@pytest.fixture(scope="module")
def matcher() -> ReplyPolicyMatcher:
    """生产 `config/resume_kb.yaml` 的真实匹配器（线上跑的就是这一份）。"""
    config = yaml.safe_load(PRODUCTION_RESUME_KB.read_text(encoding="utf-8")) or {}
    persona = config["persona"]
    instance = ReplyPolicyMatcher(
        [ReplyPolicyConfig(**policy) for policy in persona["reply_policies"]],
        persona["fallbacks"],
    )
    assert instance.enabled
    return instance


def _texts(question: dict) -> list[tuple[str, str]]:
    """(用例标签, 待测文本)——含 variants 变体问法。"""
    return [(question["id"], question["question"])] + [
        (f"{question['id']}·变体", text) for text in question.get("variants", [])
    ]


def _format(failures: list[str]) -> str:
    return "\n".join(f"  - {line}" for line in failures)


# ---------------------------------------------------------------------------
# 数据集自身的结构契约
# ---------------------------------------------------------------------------


def test_eval_set_covers_all_65_questions_in_8_groups(eval_set: dict) -> None:
    """覆盖面契约：65 问、A–H 八组、id 唯一、每题至少声明一条判定。

    钉住规模是为了防止"悄悄少了几问"——评测集的完整性本身就是它的价值。
    """
    questions = eval_set["questions"]
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), f"id 重复：{ids}"
    assert len(questions) == sum(EXPECTED_GROUP_COUNTS.values()), (
        f"总题数 {len(questions)} != {sum(EXPECTED_GROUP_COUNTS.values())}"
    )
    assert Counter(q["group"] for q in questions) == EXPECTED_GROUP_COUNTS

    undocumented = [
        q["id"]
        for q in questions
        if not (q.get("must_recall") or q.get("must_hit_policy") or q.get("must_not_intercept"))
    ]
    assert not undocumented, f"这些题没有任何零成本判定：{undocumented}"


def test_expectations_reference_real_cards_and_policies(eval_set: dict, kb: KnowledgeBase) -> None:
    """不得有悬空引用：期望卡片必须真实存在，期望话术 key 必须在配置里。"""
    card_ids = {card.id for card in kb.cards}
    fallbacks = yaml.safe_load(PRODUCTION_RESUME_KB.read_text(encoding="utf-8"))["persona"][
        "fallbacks"
    ]
    for question in eval_set["questions"]:
        missing_cards = [c for c in question.get("must_recall", []) if c not in card_ids]
        assert not missing_cards, f"{question['id']} 引用了不存在的卡片：{missing_cards}"
        if key := question.get("must_hit_policy"):
            assert key in fallbacks, f"{question['id']} 引用了未配置的话术 key：{key}"


def test_eval_set_is_pii_free(eval_set: dict) -> None:
    """数据集本身入库（公开仓库）→ 必须零 PII、零雇主实名。

    题面里出现"手机号""成绩"这类**话题词**是正常的（它测的就是拒答），
    但不得出现真实的联系方式、证件号或雇主名称。
    """
    raw = EVAL_SET_PATH.read_text(encoding="utf-8")
    for name, pattern in PII_PATTERNS.items():
        assert not pattern.search(raw), f"评测集里出现了 {name} 形态的真实 PII"
    for employer in EMPLOYER_NAMES:
        assert employer not in raw, f"评测集里出现了雇主实名「{employer}」"


# ---------------------------------------------------------------------------
# 三类行为断言（零模型）
# ---------------------------------------------------------------------------


def test_recall_expectations_hold(eval_set: dict, kb: KnowledgeBase) -> None:
    """普通提问必须能召回期望卡片（期望列命中至少一张即算通过）。"""
    top_k = int(eval_set["recall_top_k"])
    failures: list[str] = []
    for question in eval_set["questions"]:
        expected = question.get("must_recall")
        if not expected:
            continue
        for label, text in _texts(question):
            found = [card.id for card in kb.search(text, top_k=top_k)]
            if not set(expected) & set(found):
                failures.append(f"{label} {text!r} 期望 {expected}，实际 top{top_k}={found}")
    assert not failures, "召回失配：\n" + _format(failures)


def test_redline_questions_hit_expected_fixed_reply(
    eval_set: dict, matcher: ReplyPolicyMatcher
) -> None:
    """红线提问必须命中配置里的固定话术（命中即不进模型），且话术零 PII。"""
    failures: list[str] = []
    for question in eval_set["questions"]:
        expected = question.get("must_hit_policy")
        if not expected:
            continue
        for label, text in _texts(question):
            hit = matcher.match(text)
            if hit is None:
                failures.append(f"{label} {text!r} 期望命中 {expected}，实际未命中")
                continue
            if hit.reply_key != expected:
                failures.append(f"{label} 期望 {expected}，实际 {hit.reply_key}")
                continue
            assert hit.reply.strip(), f"{label} 命中了空话术"
            for name, pattern in PII_PATTERNS.items():
                assert not pattern.search(hit.reply), f"{label} 的固定话术含 {name} 形态内容"
    assert not failures, "固定话术失配：\n" + _format(failures)


def test_normal_questions_are_not_intercepted(eval_set: dict, matcher: ReplyPolicyMatcher) -> None:
    """必须放行给模型：正常技术问答不得被红线词表误伤。

    包含两部分：标注了 `must_not_intercept` 的评测集题目，以及
    `misinterception_guard`（专门验证"触发词 + scope_hints"的组合语义——
    例如「性能优化」只有落在海洋数据话题域才拦截）。
    """
    failures: list[str] = []
    for question in eval_set["questions"]:
        if not question.get("must_not_intercept"):
            continue
        for label, text in _texts(question):
            if (hit := matcher.match(text)) is not None:
                failures.append(f"{label} {text!r} 被拦为 {hit.reply_key}，应交给模型作答")
    for guard in eval_set["misinterception_guard"]:
        if (hit := matcher.match(guard["question"])) is not None:
            failures.append(
                f"{guard['id']} {guard['question']!r} 被误拦为 {hit.reply_key}"
                f"（{guard.get('note', '')}）"
            )
    assert not failures, "误拦截：\n" + _format(failures)
