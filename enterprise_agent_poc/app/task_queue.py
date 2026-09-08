"""Durable Redis queue for product tasks.

The processing list gives a restarted worker a recoverable lease. A task is
removed only after its database state is terminal, and credit charging is
already idempotent by task_id.
"""
from __future__ import annotations

from app.settings import Settings


class RedisTaskQueue:
    pending = "enterprise-agent:tasks:pending"
    processing = "enterprise-agent:tasks:processing"

    def __init__(self, redis_url: str) -> None:
        from redis import Redis
        self._client = Redis.from_url(redis_url, decode_responses=True)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisTaskQueue":
        if not settings.redis_url:
            raise RuntimeError("Redis Queue 已启用但未配置 REDIS_URL。")
        return cls(settings.redis_url)

    def ping(self) -> bool:
        return bool(self._client.ping())

    def enqueue(self, task_id: str) -> None:
        self._client.lpush(self.pending, task_id)

    def recover_processing(self) -> None:
        while self._client.rpoplpush(self.processing, self.pending):
            pass

    def reserve(self, timeout: int = 5) -> str | None:
        return self._client.brpoplpush(self.pending, self.processing, timeout=timeout)

    def acknowledge(self, task_id: str) -> None:
        self._client.lrem(self.processing, 1, task_id)
