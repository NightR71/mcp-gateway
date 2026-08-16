"""令牌桶限流（自实现，展示原理）：按 API Key 维度限流。

原理：每个 Key 一个桶，容量 = 每分钟配额；令牌按「配额 / 60」个每秒匀速补充，
请求到来消耗 1 个令牌，桶空则拒绝（上层转 429）。
纯内存态、单事件循环内无 await，天然无竞态。
"""

import time

from app.schemas.auth import APIKeyInfo


class TokenBucket:
    """单个 API Key 的令牌桶。"""

    def __init__(self, rate_per_minute: int) -> None:
        self.capacity = float(rate_per_minute)
        self.refill_per_second = rate_per_minute / 60.0
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

    def try_consume(self) -> bool:
        """尝试消耗 1 个令牌：成功返回 True，桶空返回 False。"""
        self._refill()
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def retry_after(self) -> float:
        """估算再攒够 1 个令牌所需的秒数（供 429 的 Retry-After 头使用）。"""
        if self.refill_per_second <= 0:
            return 60.0
        return max(0.0, (1.0 - self.tokens) / self.refill_per_second)


class RateLimiter:
    """按 API Key 维度管理令牌桶（惰性创建）。"""

    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, api_key: APIKeyInfo) -> tuple[bool, float]:
        """检查是否放行；返回 (是否放行, 拒绝时建议的重试秒数)。"""
        bucket = self._buckets.get(api_key.key)
        if bucket is None:
            bucket = TokenBucket(api_key.rate_limit_per_minute)
            self._buckets[api_key.key] = bucket
        if bucket.try_consume():
            return True, 0.0
        return False, bucket.retry_after()
