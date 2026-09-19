"""令牌桶限流（自实现，展示原理）：按 API Key 维度限流。

- 分钟桶（默认）：每个 Key 一个桶，容量 = 每分钟配额；令牌按「配额 / 60」个
  每秒匀速补充，请求到来消耗 1 个令牌，桶空则拒绝（上层转 429）。
- 小时桶（M1）：`APIKeyInfo.rate_limit_per_hour` 配置时叠加启用——容量 = 每小时
  配额，令牌按「配额 / 3600」每秒慢速补充（相比整点清零，平滑突发且让
  Retry-After 有确定语义）；两桶须同时有令牌才放行。
纯内存态、单事件循环内无 await，天然无竞态。
"""

import time

from app.schemas.auth import APIKeyInfo


class TokenBucket:
    """单个 API Key 的令牌桶：容量 rate，每 period_seconds 匀速补满一轮。

    默认 period_seconds=60 即分钟桶（容量 = 每分钟配额，按 配额/60 每秒补充）。
    """

    def __init__(self, rate_per_minute: int, *, period_seconds: float = 60.0) -> None:
        self.capacity = float(rate_per_minute)
        self.period_seconds = period_seconds
        self.refill_per_second = rate_per_minute / period_seconds
        self.tokens = self.capacity
        self.last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.last_refill = now

    def has_token(self) -> bool:
        """预检是否有令牌（补充但不消耗）——多桶联合限流时避免单边空耗。"""
        self._refill()
        return self.tokens >= 1.0

    def consume(self) -> None:
        """消耗 1 个令牌（调用前应先用 has_token 确认有令牌）。"""
        self.tokens -= 1.0

    def try_consume(self) -> bool:
        """尝试消耗 1 个令牌：成功返回 True，桶空返回 False。"""
        if not self.has_token():
            return False
        self.consume()
        return True

    def retry_after(self) -> float:
        """估算再攒够 1 个令牌所需的秒数（供 429 的 Retry-After 头使用）。"""
        if self.refill_per_second <= 0:
            return self.period_seconds
        return max(0.0, (1.0 - self.tokens) / self.refill_per_second)


class HourTokenBucket(TokenBucket):
    """小时桶（M1）：容量 = 每小时配额，令牌按 配额/3600 每秒慢速补充。"""

    def __init__(self, rate_per_hour: int) -> None:
        super().__init__(rate_per_hour, period_seconds=3600.0)


class RateLimiter:
    """按 API Key 维度管理令牌桶（惰性创建）：分钟桶 + 可选小时桶。"""

    def __init__(self) -> None:
        self._minute_buckets: dict[str, TokenBucket] = {}
        self._hour_buckets: dict[str, TokenBucket] = {}

    def _buckets_for(self, api_key: APIKeyInfo) -> list[TokenBucket]:
        """取（或惰性创建）该 Key 的全部生效桶：分钟桶必有，小时桶按配置。"""
        minute = self._minute_buckets.get(api_key.key)
        if minute is None:
            minute = TokenBucket(api_key.rate_limit_per_minute)
            self._minute_buckets[api_key.key] = minute
        buckets = [minute]
        if api_key.rate_limit_per_hour is not None:
            hour = self._hour_buckets.get(api_key.key)
            if hour is None:
                hour = HourTokenBucket(api_key.rate_limit_per_hour)
                self._hour_buckets[api_key.key] = hour
            buckets.append(hour)
        return buckets

    def check(self, api_key: APIKeyInfo) -> tuple[bool, float]:
        """检查是否放行；返回 (是否放行, 拒绝时建议的重试秒数)。

        两阶段检查：先预检所有桶都有令牌、再统一消耗——避免「A 桶已扣令牌、
        B 桶却拒绝」的单边空耗。拒绝时 Retry-After 取各未通过桶的最大值。
        """
        buckets = self._buckets_for(api_key)
        rejected = [bucket for bucket in buckets if not bucket.has_token()]
        if rejected:
            return False, max(bucket.retry_after() for bucket in rejected)
        for bucket in buckets:
            bucket.consume()
        return True, 0.0
