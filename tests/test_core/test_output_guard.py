"""输出守门测试（M3）：PII 打码（一次性 + 流式滑动窗口）。

流式滑窗是重点：逐 token 输出时 PII 可能被切成多段（`138` + `1234` + `5678`），
必须保证不完整候选不会被提前下发、也不从匹配中间切开。
"""

from app.config import OutputGuardConfig, PIIPatternConfig
from app.core.output_guard import OutputGuard

PHONE_PATTERN = PIIPatternConfig(
    name="phone", pattern=r"(?<!\d)1[3-9]\d{9}(?!\d)", keep_prefix=3, keep_suffix=4
)
EMAIL_PATTERN = PIIPatternConfig(
    name="email",
    pattern=r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    keep_prefix=1,
    keep_suffix=0,
)
ID_PATTERN = PIIPatternConfig(
    name="id_card", pattern=r"(?<!\d)\d{17}[0-9Xx](?!\d)", keep_prefix=3, keep_suffix=2
)


def _guard(*patterns: PIIPatternConfig, window: int = 64) -> OutputGuard:
    return OutputGuard(
        OutputGuardConfig(enabled=True, window_chars=window, patterns=list(patterns))
    )


# ---------------------------------------------------------------------------
# 一次性打码
# ---------------------------------------------------------------------------


def test_mask_phone_keeps_ends() -> None:
    """手机号保留前 3 后 4，中间打码且长度不变（流式渲染不跳动）。"""
    guard = _guard(PHONE_PATTERN)
    masked = guard.mask_text("联系电话 13800001111 谢谢")
    assert masked == "联系电话 138****1111 谢谢"
    assert len(masked) == len("联系电话 13800001111 谢谢")


def test_mask_email_keeps_first_char() -> None:
    """邮箱保留首字符，其余打码（域名同属可识别信息，一并遮蔽）。"""
    guard = _guard(EMAIL_PATTERN)
    masked = guard.mask_text("邮箱是 zhangsan@example.com 请查收")
    assert "zhangsan@example.com" not in masked
    assert masked.startswith("邮箱是 z")
    assert "@" not in masked


def test_mask_id_card() -> None:
    """身份证保留前 3 后 2。"""
    guard = _guard(ID_PATTERN)
    masked = guard.mask_text("证件号 110101199003071234")
    assert masked == "证件号 110*************34"


def test_mask_multiple_patterns_together() -> None:
    """多模式共存：一条文本里的手机号与邮箱都被打码。"""
    guard = _guard(PHONE_PATTERN, EMAIL_PATTERN)
    masked = guard.mask_text("手机 13800001111，邮箱 abc@def.com")
    assert "13800001111" not in masked
    assert "abc@def.com" not in masked


def test_plain_text_untouched() -> None:
    """正常内容不被改动（守门不得误伤可读性）。"""
    guard = _guard(PHONE_PATTERN, EMAIL_PATTERN)
    text = "我用 FastAPI 写了网关，196 项测试通过，语义路由用中文 2-gram 分词。"
    assert guard.mask_text(text) == text


def test_disabled_guard_is_passthrough() -> None:
    """守门关闭时原样返回（含 PII）——配置化开关，行为可预期。"""
    guard = OutputGuard(OutputGuardConfig(enabled=False, patterns=[PHONE_PATTERN]))
    assert not guard.enabled
    assert guard.mask_text("13800001111") == "13800001111"


def test_invalid_pattern_is_skipped() -> None:
    """非法正则不致命：跳过该条，其余规则照常生效。"""
    guard = _guard(PIIPatternConfig(name="broken", pattern="([unclosed"), PHONE_PATTERN)
    assert guard.rule_names == ("phone",)
    assert guard.mask_text("13800001111") == "138****1111"


def test_boundary_digits_not_masked() -> None:
    """贴邻数字的长数字串不算手机号（避免误伤，靠前后向断言）。"""
    guard = _guard(PHONE_PATTERN)
    text = "订单号 9138000011110000"
    assert guard.mask_text(text) == text


# ---------------------------------------------------------------------------
# 流式滑动窗口
# ---------------------------------------------------------------------------


def test_stream_single_chunk_phone() -> None:
    """整段输入：切点保护 + 尾部候选 hold，flush 后完整打码。"""
    guard = _guard(PHONE_PATTERN)
    masker = guard.new_stream()
    out = masker.feed("联系电话是 13800001111")
    out += masker.flush()
    assert "13800001111" not in out
    assert out == "联系电话是 138****1111"


def test_stream_phone_split_across_chunks() -> None:
    """手机号被切成三段：任何中间片段都不得提前泄露。"""
    guard = _guard(PHONE_PATTERN, window=8)
    masker = guard.new_stream()
    out = ""
    for chunk in ("我的号码 138", "1234", "5678 联系我"):
        emitted = masker.feed(chunk)
        assert "13812345678" not in emitted
        out += emitted
    out += masker.flush()
    assert out == "我的号码 138****5678 联系我"
    assert "1234" not in out


def test_stream_email_split_across_chunks() -> None:
    """邮箱逐字符流式：不得泄露用户名或域名片段。"""
    guard = _guard(EMAIL_PATTERN, window=16)
    masker = guard.new_stream()
    out = ""
    for char in "邮箱 abc@example.com 收到":
        out += masker.feed(char)
    out += masker.flush()
    assert "abc@example.com" not in out
    assert "example.com" not in out
    assert out.startswith("邮箱 a")


def test_stream_does_not_split_matching_number() -> None:
    """切点保护：即使窗口边界落在手机号中间，也不从中间切开（否则后半段会逃逸）。"""
    guard = _guard(PHONE_PATTERN, window=6)
    masker = guard.new_stream()
    out = masker.feed("前缀填充文字很长很长 13800001111")
    out += masker.flush()
    assert "13800001111" not in out
    # 泄露检查：任何 4 位以上的连续原始号码片段都不应出现
    assert "3800" not in out and "0011" not in out


def test_stream_masked_full_matches_emitted() -> None:
    """masked_full 恒等于已下发文本的拼接（runner 用它做最终 answer）。"""
    guard = _guard(PHONE_PATTERN, EMAIL_PATTERN, window=12)
    masker = guard.new_stream()
    pieces = []
    for chunk in ("联系 138", "0000", "1111 或 a@b.com", " 结束"):
        pieces.append(masker.feed(chunk))
    pieces.append(masker.flush())
    assert masker.masked_full == "".join(pieces)


def test_stream_plain_text_is_not_held_forever() -> None:
    """普通文本在窗口外即下发（hold 只作用于尾部候选，不整体滞留）。"""
    guard = _guard(PHONE_PATTERN, window=16)
    masker = guard.new_stream()
    first = masker.feed("这是一段完全正常的介绍文字，不含任何敏感信息。")
    assert first  # 窗口外的部分已下发
    assert masker.flush()  # 收尾仍能拿到剩余


def test_stream_instances_are_independent() -> None:
    """两个流各自持有 buffer（AgentRunner 单例并发服务时互不串扰）。"""
    guard = _guard(PHONE_PATTERN, window=8)
    first, second = guard.new_stream(), guard.new_stream()
    first.feed("甲 1380000")
    out = second.feed("乙 13700000000") + second.flush()
    assert "13700000000" not in out
    assert "137" in out  # 第二个流自己的号码按规则打码（保留前 3 位）
    # 第一个流只包含自己的内容，且没有第二个流的任何字符（无共享 buffer）
    assert first.masked_full == "甲"
    assert "137" not in first.masked_full
