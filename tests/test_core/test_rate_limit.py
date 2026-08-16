"""令牌桶限流单元测试。"""

import time

import pytest

from app.core.rate_limit import RateLimiter, TokenBucket
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
