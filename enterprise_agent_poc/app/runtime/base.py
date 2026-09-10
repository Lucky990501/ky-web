from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain import RuntimeProfile, RuntimeSession, RuntimeTurn


class RuntimeStartError(RuntimeError):
    """A startup failure whose stage is safe to retain in product traces."""

    def __init__(self, stage: str) -> None:
        super().__init__("Codex Runtime 未能启动。")
        self.stage = stage


class RuntimeProvider(ABC):
    @abstractmethod
    async def create_session(self, profile: RuntimeProfile, developer_instructions: str) -> RuntimeSession: ...

    @abstractmethod
    async def resume_session(
        self,
        profile: RuntimeProfile,
        thread_id: str,
        developer_instructions: str | None = None,
        recovery_context: str | None = None,
    ) -> RuntimeSession: ...

    @abstractmethod
    async def run_turn(self, session: RuntimeSession, message: str) -> RuntimeTurn: ...

    @abstractmethod
    async def close(self) -> None: ...
