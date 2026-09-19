"""令牌桶限流单元测试。"""

import time

import pytest

from app.core.rate_limit import HourTokenBucket, RateLimiter, TokenBucket
from app.schemas.auth import APIKeyInfo


def test_token_bucket_allows_up_to_capacity() -> None:
    """桶满时可连续放行 capacity 次，之后拒绝。"""
    bucket = TokenBucket(rate_per_minute=3)
    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_token_bucket_refills_over_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """令牌按速率匀速补充，且不超过容量。"""
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    bucket = TokenBucket(rate_per_minute=2)  # 容量 2，补充速率 1 个/30 秒

    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False

    now += 30.0  # 过 30 秒，补 1 个令牌
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False

    now += 120.0  # 过 120 秒，应补 4 个但 capped 在容量 2
    assert bucket.try_consume() is True
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_retry_after_positive_when_rejected() -> None:
    bucket = TokenBucket(rate_per_minute=1)
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False
    assert bucket.retry_after() > 0


def test_rate_limiter_per_key_isolation() -> None:
    """不同 Key 的桶互相独立。"""
    limiter = RateLimiter()
    key_a = APIKeyInfo(key="a", name="a", rate_limit_per_minute=1)
    key_b = APIKeyInfo(key="b", name="b", rate_limit_per_minute=1)

    allowed, _ = limiter.check(key_a)
    assert allowed is True
    allowed, retry_after = limiter.check(key_a)
    assert allowed is False
    assert retry_after > 0

    allowed, _ = limiter.check(key_b)
    assert allowed is True


# ---------- M1：小时桶（容量 = 每小时配额，慢速补充） ----------


def test_hour_token_bucket_allows_up_to_capacity() -> None:
    """小时桶满时可连续放行 capacity 次，之后拒绝。"""
    bucket = HourTokenBucket(50)
    for _ in range(50):
        assert bucket.try_consume() is True
    assert bucket.try_consume() is False


def test_hour_token_bucket_slow_refill(monkeypatch: pytest.MonkeyPatch) -> None:
    """慢速补充：50/h ≈ 1 个/72 秒——时间过半也只补零头，而非整点放整桶。"""
    now = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: now)
    bucket = HourTokenBucket(50)

    for _ in range(50):
        bucket.try_consume()
    assert bucket.try_consume() is False

    now += 36.0  # 过半小时只应累积 0.5 个，不够一次请求
    assert bucket.try_consume() is False

    now += 37.0  # 累计 73 秒 ≈ 1 个令牌
    assert bucket.try_consume() is True
    assert bucket.try_consume() is False  # 只补了约 1 个，不是整桶


def test_retry_after_hour_scale() -> None:
    """小时桶耗尽后 Retry-After 为小时级（50/h → 攒 1 个约 72 秒）。"""
    bucket = HourTokenBucket(50)
    for _ in range(50):
        bucket.try_consume()
    assert 71.0 < bucket.retry_after() < 73.0


def test_rate_limiter_hour_bucket_rejects_even_if_minute_has_tokens() -> None:
    """小时额度耗尽 → 拒绝（即使分钟额度充足）；Retry-After 为小时级。"""
    limiter = RateLimiter()
    key = APIKeyInfo(key="h", name="h", rate_limit_per_minute=100, rate_limit_per_hour=2)

    assert limiter.check(key)[0] is True
    assert limiter.check(key)[0] is True
    allowed, retry_after = limiter.check(key)
    assert allowed is False
    assert retry_after > 60.0  # 小时桶的补充节奏远慢于分钟桶


def test_rate_limiter_minute_bucket_rejects_even_if_hour_has_tokens() -> None:
    """分钟额度耗尽 → 照常拒绝（小时桶配置不改变分钟桶语义）。"""
    limiter = RateLimiter()
    key = APIKeyInfo(key="m", name="m", rate_limit_per_minute=1, rate_limit_per_hour=100)

    assert limiter.check(key)[0] is True
    allowed, retry_after = limiter.check(key)
    assert allowed is False
    assert 0.0 < retry_after <= 60.0


def test_rate_limiter_no_cross_bucket_waste() -> None:
    """两阶段检查：小时桶拒绝时分钟桶不被空耗（令牌不减少）。"""
    limiter = RateLimiter()
    key = APIKeyInfo(key="w", name="w", rate_limit_per_minute=5, rate_limit_per_hour=1)

    assert limiter.check(key)[0] is True  # 两桶各扣 1
    assert limiter.check(key)[0] is False  # 小时桶空 → 拒绝

    minute_bucket = limiter._minute_buckets[key.key]
    for _ in range(4):  # 分钟桶仍剩 4 个令牌可用（未被拒绝请求消耗）
        assert minute_bucket.has_token() is True
        minute_bucket.consume()
    assert minute_bucket.has_token() is False


def test_rate_limiter_no_hour_bucket_by_default() -> None:
    """未配置 rate_limit_per_hour 的 Key 行为与旧版一致：只有分钟桶。"""
    limiter = RateLimiter()
    key = APIKeyInfo(key="d", name="d", rate_limit_per_minute=2)
    assert limiter._hour_buckets == {}

    assert limiter.check(key)[0] is True
    assert limiter.check(key)[0] is True
    assert limiter.check(key)[0] is False
