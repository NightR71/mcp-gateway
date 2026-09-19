"""简历 Agent 人设层（M3）：系统提示词装配 + 输入侧固定回应策略匹配。

执行计划 §5.1 第 2 层防御的落地：
- **人设规则**：客观克制的「技术代言人」，基于知识库作答、主动引导、禁止闲聊与
  过度拟人化、归属照实、防注入——条款全部来自配置（`resume_kb.persona`）；
- **固定回应**：命中红线话题（联系方式/成绩/薪资/内部信息/注入/越界任务）时
  直接返回配置里的固定话术，**不进模型**——确定性拦截，省 token 且零幻觉。

代码只负责「拼装」与「匹配」，任何文案都不写死在代码里。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import PersonaConfig, ReplyPolicyConfig, ResumeKBConfig


@dataclass(frozen=True)
class PolicyHit:
    """一次固定回应命中。"""

    policy_id: str
    reply_key: str
    reply: str
    matched: tuple[str, ...]  # 命中的关键词（日志/调试用，不外发）


def _normalize(text: str) -> str:
    """匹配前归一化：小写 + 去空白（中英混排输入的关键词命中更稳）。"""
    return "".join(text.lower().split())


class ReplyPolicyMatcher:
    """输入侧固定回应匹配器（无状态，可并发使用）。"""

    def __init__(self, policies: list[ReplyPolicyConfig], fallbacks: dict[str, str]) -> None:
        self._policies = policies
        self._fallbacks = fallbacks

    @property
    def enabled(self) -> bool:
        return bool(self._policies and self._fallbacks)

    def fallback(self, reply_key: str) -> str:
        """取固定话术；未配置时返回空串（调用方据此跳过）。"""
        return self._fallbacks.get(reply_key, "")

    def match(self, question: str) -> PolicyHit | None:
        """按配置顺序匹配：命中即返回（顺序即优先级）；无命中返回 None。"""
        if not self.enabled or not question:
            return None
        normalized = _normalize(question)
        for policy in self._policies:
            triggers = tuple(t for t in policy.trigger_patterns if _normalize(t) in normalized)
            if not triggers:
                continue
            # scope_hints 非空时要求同时落在该话题域内（避免红线误伤其他项目问答）
            if policy.scope_hints and not any(
                _normalize(hint) in normalized for hint in policy.scope_hints
            ):
                continue
            reply = self._fallbacks.get(policy.reply_key, "")
            if not reply:
                continue
            return PolicyHit(
                policy_id=policy.id,
                reply_key=policy.reply_key,
                reply=reply,
                matched=triggers,
            )
        return None

    def out_of_scope_reply(self) -> str:
        """检索无命中/知识库未覆盖时的统一兜底话术。"""
        return self._fallbacks.get("out_of_scope", "")


def build_system_prompt(config: PersonaConfig, *, fallbacks: dict[str, str] | None = None) -> str:
    """把配置拼装成系统提示词（人设 + 规则 + 红线 + 兜底话术清单）。

    返回空串表示未启用（调用方据此不注入 system 消息，行为与旧版一致）。
    """
    if not config.enabled:
        return ""

    sections: list[str] = []
    header = f"你是候选人的{config.name}" if config.name else "你是候选人的技术代言人"
    sections.append(header + ("。" + config.intro if config.intro else ""))

    if config.rules:
        sections.append(
            "【行为规则】\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(config.rules, 1))
        )
    if config.redlines:
        sections.append("【绝对禁止】\n" + "\n".join(f"- {r}" for r in config.redlines))

    text = fallbacks if fallbacks is not None else config.fallbacks
    if text:
        lines = [f"- {key}：{value}" for key, value in text.items()]
        sections.append(
            "【固定话术】遇到下列情形时，原样使用对应话术回答（不要改写、不要补充）：\n"
            + "\n".join(lines)
        )

    return "\n\n".join(section for section in sections if section)


@dataclass
class ResumePersona:
    """人设层聚合：系统提示词 + 固定回应匹配器（由 ResumeKBConfig 构造）。"""

    system_prompt: str
    matcher: ReplyPolicyMatcher

    @property
    def enabled(self) -> bool:
        return bool(self.system_prompt)

    @classmethod
    def from_config(cls, config: ResumeKBConfig) -> ResumePersona:
        persona = config.persona
        return cls(
            system_prompt=build_system_prompt(persona),
            matcher=ReplyPolicyMatcher(persona.reply_policies, persona.fallbacks),
        )
