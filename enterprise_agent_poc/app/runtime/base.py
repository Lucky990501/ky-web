from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain import RuntimeProfile, RuntimeSession, RuntimeTurn


class RuntimeProvider(ABC):
    @abstractmethod
    async def create_session(self, profile: RuntimeProfile, developer_instructions: str) -> RuntimeSession: ...

    @abstractmethod
    async def resume_session(self, profile: RuntimeProfile, thread_id: str) -> RuntimeSession: ...

    @abstractmethod
    async def run_turn(self, session: RuntimeSession, message: str) -> RuntimeTurn: ...

    @abstractmethod
    async def close(self) -> None: ...
