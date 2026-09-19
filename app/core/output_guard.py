"""输出守门（M3，执行计划 §5.1 第 3 层）：对最终回答做 PII 模式扫描与打码。

两层用法：
- `OutputGuard.mask_text(text)`：一次性打码（用于非流式回答、步骤 Trace、最终 answer）；
- `OutputGuard.new_stream()`：**流式滑动窗口**掩码器——逐 token 喂入，返回可安全
  下发的文本。因为一次回答的 PII 可能被切成多个 token（如 `138` + `1234` + `5678`），
  掩码器 hold 住尾部窗口，保证不完整候选不会被提前发出，也不从匹配中间切开。

并发安全：`OutputGuard` 本身无状态（只有编译好的配置），流式状态在每个
`StreamMasker` 实例里——AgentRunner 单例可并发服务多请求，各自持有独立掩码器。

配置驱动（不写死）：模式、保留前后位数、掩码字符、窗口大小全部来自 YAML。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import OutputGuardConfig, PIIPatternConfig

# 尾部「可能是某个 PII 模式前缀」的候选检测：命中则把这些字符 hold 到下一轮，
# 避免不完整候选（如手机号只到了 8 位）被提前输出后无法再匹配。
_PARTIAL_TAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"1[3-9]\d{0,8}$"),  # 手机号前缀（未满 11 位）
    re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9.\-]{0,64}(?:\.[A-Za-z]{0,16})?$"),  # 邮箱前缀
    re.compile(r"\d{1,16}[Xx]?$"),  # 身份证前缀（也覆盖尾部任意数字串）
)


@dataclass(frozen=True)
class MaskRule:
    """一条编译好的打码规则。"""

    name: str
    regex: re.Pattern[str]
    keep_prefix: int
    keep_suffix: int
    mask_char: str

    def apply(self, match: re.Match[str]) -> str:
        """等长打码：保留前 keep_prefix / 后 keep_suffix 个字符，中间替换为掩码字符。"""
        text = match.group(0)
        keep_prefix = min(self.keep_prefix, len(text))
        keep_suffix = min(self.keep_suffix, max(0, len(text) - keep_prefix))
        end = len(text) - keep_suffix if keep_suffix else len(text)
        masked = self.mask_char * (end - keep_prefix)
        return text[:keep_prefix] + masked + text[end:]


def _compile(config: PIIPatternConfig) -> MaskRule | None:
    """编译单条配置；正则非法时跳过（配置错误不应导致整个回答不可用）。"""
    try:
        regex = re.compile(config.pattern)
    except re.error:
        return None
    return MaskRule(
        name=config.name,
        regex=regex,
        keep_prefix=config.keep_prefix,
        keep_suffix=config.keep_suffix,
        mask_char=config.mask_char or "*",
    )


class OutputGuard:
    """PII 输出守门（无状态配置对象）：可按需创建一次性流式掩码器。"""

    def __init__(self, config: OutputGuardConfig) -> None:
        self._config = config
        self._rules: tuple[MaskRule, ...] = tuple(
            rule for rule in (_compile(p) for p in config.patterns) if rule is not None
        )

    @property
    def enabled(self) -> bool:
        return self._config.enabled and bool(self._rules)

    @property
    def rule_names(self) -> tuple[str, ...]:
        return tuple(rule.name for rule in self._rules)

    def mask_text(self, text: str) -> str:
        """一次性打码（非流式路径）。守门关闭或无规则时原样返回。"""
        if not self.enabled or not text:
            return text
        for rule in self._rules:
            text = rule.regex.sub(rule.apply, text)
        return text

    def new_stream(self) -> StreamMasker:
        """创建一次性流式掩码器（每次回答一个实例）。"""
        return StreamMasker(self, self._config.window_chars)


class StreamMasker:
    """流式滑窗掩码器：hold 尾部窗口 → 安全切点 → 打码后下发。

    切点两条约束：
    1. 不切开一个已完整出现的匹配（回退到匹配起点）；
    2. 尾部若存在「未完成的 PII 候选」，把切点收到候选起点。
    保证任何跨 token 拼接出来的 PII 都会被完整看到后再打码。
    """

    def __init__(self, guard: OutputGuard, window_chars: int) -> None:
        self._guard = guard
        self._window = max(1, window_chars)
        self._buffer = ""
        self._emitted: list[str] = []

    @property
    def masked_full(self) -> str:
        """截至目前已下发的完整（打码后）文本。"""
        return "".join(self._emitted)

    def feed(self, chunk: str) -> str:
        """喂入一个文本增量，返回本次可安全下发的已打码文本（可能为空串）。"""
        if not chunk:
            return ""
        if not self._guard.enabled:
            self._emitted.append(chunk)
            return chunk
        self._buffer += chunk
        if len(self._buffer) <= self._window:
            return ""
        cut = self._adjust_cut(len(self._buffer) - self._window)
        if cut <= 0:
            return ""
        return self._emit(cut)

    def flush(self) -> str:
        """流结束：下发 buffer 剩余内容（已打码）。"""
        if not self._guard.enabled:
            return ""
        if not self._buffer:
            return ""
        return self._emit(len(self._buffer))

    # -- 内部 ---------------------------------------------------------------

    def _emit(self, cut: int) -> str:
        emit, self._buffer = self._buffer[:cut], self._buffer[cut:]
        masked = self._guard.mask_text(emit)
        self._emitted.append(masked)
        return masked

    def _adjust_cut(self, cut: int) -> int:
        """把切点向前收缩到安全位置（不切开完整匹配 / 不截断未完成候选）。"""
        for rule in self._guard._rules:  # noqa: SLF001（同类内部协作）
            for match in rule.regex.finditer(self._buffer):
                if match.start() < cut < match.end():
                    cut = match.start()
        partial = self._partial_start()
        if partial is not None and partial < cut:
            cut = partial
        return cut

    def _partial_start(self) -> int | None:
        """尾部未完成候选的起点（无候选返回 None）。"""
        start: int | None = None
        for pattern in _PARTIAL_TAIL_PATTERNS:
            match = pattern.search(self._buffer)
            if match is not None and (start is None or match.start() < start):
                start = match.start()
        return start
