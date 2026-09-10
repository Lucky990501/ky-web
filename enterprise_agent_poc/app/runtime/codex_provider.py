from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from app.domain import RuntimeProfile, RuntimeSession, RuntimeTurn, SandboxPolicy
from app.security import RuntimePrincipal, RuntimeTokenIssuer
from app.skills import SkillDeployment
from app.settings import Settings
from app.runtime.base import RuntimeProvider, RuntimeStartError


@dataclass(slots=True)
class _Instance:
    codex: object
    profile: RuntimeProfile
    last_active_at: float
    token_expires_at: int


class CodexRuntimeManager:
    """Owns one local Codex App Server client per tenant-agent Runtime Profile."""

    def __init__(self, settings: Settings, deployment: SkillDeployment, token_issuer: RuntimeTokenIssuer) -> None:
        self._settings = settings
        self._deployment = deployment
        self._token_issuer = token_issuer
        self._instances: dict[str, _Instance] = {}
        self._startup_events: dict[str, list[dict]] = {}
        self._lock = asyncio.Lock()

    def _start_event(self, profile: RuntimeProfile, event: str, **details: str) -> None:
        self._startup_events.setdefault(profile.id, []).append({"event": event, **details})

    def startup_events(self, profile: RuntimeProfile) -> tuple[dict, ...]:
        """Return only lifecycle labels/stages; never provider error text or secrets."""
        return tuple(dict(event) for event in self._startup_events.get(profile.id, ()))

    def _paths(self, profile: RuntimeProfile) -> tuple[Path, Path]:
        root = self._settings.data_dir / "runtime" / profile.tenant_id / profile.agent_id / profile.id
        return root / "codex-home", root / "workspace"

    def _prepare_profile(self, profile: RuntimeProfile) -> tuple[Path, Path, str, int]:
        codex_home, workspace = self._paths(profile)
        workspace.mkdir(parents=True, exist_ok=True)
        skills_dir = codex_home / "skills"
        self._deployment.deploy(profile.skill_manifest, skills_dir)
        token_expires_at = int(time.time()) + 15 * 60
        scopes = ["enterprise_config:read", "knowledge:search", "assets:search"]
        if profile.agent_id == "image-agent":
            scopes.append("image:generate")
        token = self._token_issuer.issue(
            RuntimePrincipal(
                tenant_id=profile.tenant_id,
                agent_id=profile.agent_id,
                runtime_profile_id=profile.id,
                scopes=tuple(scopes),
                expires_at=token_expires_at,
            )
        )
        codex_home.mkdir(parents=True, exist_ok=True)
        provider_lines = []
        if self._settings.model_provider_id == "deepseek":
            catalog_path = codex_home / "models.json"
            catalog_path.write_text(json.dumps({"models": [self._deepseek_model_catalog(profile.model_id)]}), encoding="utf-8")
            provider_lines = [
                f'model = "{profile.model_id}"',
                'model_provider = "deepseek"',
                f'model_reasoning_effort = "{profile.reasoning_effort}"',
                f'model_catalog_json = "{catalog_path.as_posix()}"',
                "",
                "[model_providers.deepseek]",
                'name = "deepseek"',
                f'base_url = "{self._settings.model_base_url}"',
                f'wire_api = "{self._settings.model_wire_api}"',
                f'env_key = "{self._settings.codex_api_key_env}"',
                "requires_openai_auth = false",
                "",
            ]
        (codex_home / "config.toml").write_text(
            "\n".join(
                provider_lines + [
                    "[mcp_servers.platform]",
                    f'url = "{self._settings.platform_mcp_url}"',
                    'bearer_token_env_var = "PLATFORM_MCP_TOKEN"',
                    "required = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return codex_home, workspace, token, token_expires_at

    @staticmethod
    def _deepseek_model_catalog(model_id: str | None) -> dict:
        if model_id != "deepseek-v4-pro":
            raise ValueError("DeepSeek Gate 2 仅允许模型 deepseek-v4-pro。")
        return {
            "slug": "deepseek-v4-pro",
            "display_name": "DeepSeek-V4-Pro",
            "description": "DeepSeek V4 Pro for Codex Runtime POC.",
            "context_window": 1048576,
            "max_context_window": 1048576,
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "default_reasoning_level": "high",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Lower reasoning"},
                {"effort": "high", "description": "High reasoning"},
                {"effort": "max", "description": "Maximum reasoning"},
            ],
            "input_modalities": ["text"],
            "supports_parallel_tool_calls": True,
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text",
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": 2,
            "availability_nux": None,
            "upgrade": None,
            "model_messages": {
                "instructions_template": "You are Codex. Follow developer instructions and tool policies.",
            },
            "support_verbosity": True,
            "default_verbosity": "low",
            "experimental_supported_tools": [],
            "minimal_client_version": "0.144.0",
        }

    async def get(self, profile: RuntimeProfile):
        async with self._lock:
            self._startup_events[profile.id] = [{"event": "runtime_start_requested"}]
            current = self._instances.get(profile.id)
            if current and current.token_expires_at > int(time.time()) + 60:
                current.last_active_at = time.monotonic()
                self._start_event(profile, "app_server_ready")
                return current.codex
            if current:
                await current.codex.close()
                del self._instances[profile.id]
            stage = "runtime_profile"
            try:
                from openai_codex import AsyncCodex, CodexConfig
                codex_home, workspace, token, token_expires_at = self._prepare_profile(profile)
                stage = "provider_initialization"
                api_key = os.environ.get(self._settings.codex_api_key_env)
                if not api_key:
                    raise RuntimeError("Codex API Key 未配置。")
                env = {
                    "CODEX_HOME": str(codex_home),
                    "PLATFORM_MCP_TOKEN": token,
                    # The secret stays process-local: it is never written into
                    # config.toml, the database, trace payloads, or source files.
                    self._settings.codex_api_key_env: api_key,
                }
                codex = AsyncCodex(CodexConfig(cwd=str(workspace), env=env))
                self._start_event(profile, "runtime_process_created")
                stage = "app_server"
                await codex.__aenter__()
                if profile.model_provider_id != "deepseek":
                    await codex.login_api_key(api_key)
                self._start_event(profile, "app_server_ready")
                self._instances[profile.id] = _Instance(
                    codex=codex,
                    profile=profile,
                    last_active_at=time.monotonic(),
                    token_expires_at=token_expires_at,
                )
                return codex
            except Exception as exc:
                self._start_event(profile, "runtime_start_failed", stage=stage)
                if 'codex' in locals():
                    try:
                        await codex.close()
                    except Exception:
                        pass
                raise RuntimeStartError(stage) from exc

    async def close(self) -> None:
        async with self._lock:
            instances, self._instances = list(self._instances.values()), {}
        for instance in instances:
            await instance.codex.close()


class CodexRuntimeProvider(RuntimeProvider):
    def __init__(self, manager: CodexRuntimeManager) -> None:
        self._manager = manager
        self._profiles: dict[str, RuntimeProfile] = {}
        self._threads: dict[str, object] = {}

    @staticmethod
    def _sandbox(policy: SandboxPolicy):
        from openai_codex import Sandbox

        return Sandbox.read_only if policy is SandboxPolicy.READ_ONLY else Sandbox.workspace_write

    async def create_session(self, profile: RuntimeProfile, developer_instructions: str) -> RuntimeSession:
        codex = await self._manager.get(profile)
        self._manager._start_event(profile, "thread_start_requested")
        try:
            thread = await codex.thread_start(
                cwd=str(self._manager._paths(profile)[1]),
                developer_instructions=developer_instructions,
                model=profile.model_id,
                config={"model_reasoning_effort": profile.reasoning_effort},
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
        except Exception as exc:
            self._manager._start_event(profile, "runtime_start_failed", stage="thread_start")
            raise RuntimeStartError("thread_start") from exc
        self._manager._start_event(profile, "thread_started")
        self._profiles[profile.id] = profile
        self._threads[thread.id] = thread
        return RuntimeSession(thread_id=thread.id, profile_id=profile.id)

    def startup_events(self, profile: RuntimeProfile) -> tuple[dict, ...]:
        return self._manager.startup_events(profile)

    async def resume_session(
        self,
        profile: RuntimeProfile,
        thread_id: str,
        developer_instructions: str | None = None,
        recovery_context: str | None = None,
    ) -> RuntimeSession:
        codex = await self._manager.get(profile)
        self._manager._start_event(profile, "thread_resume_requested")
        try:
            thread = await codex.thread_resume(
                thread_id,
                cwd=str(self._manager._paths(profile)[1]),
                model=profile.model_id,
                config={"model_reasoning_effort": profile.reasoning_effort},
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
            self._manager._start_event(profile, "thread_resumed")
        except Exception as exc:
            if "no rollout found for thread id" not in str(exc).lower() or not developer_instructions:
                raise
            self._manager._start_event(profile, "thread_resume_unavailable")
            recovered_instructions = developer_instructions
            if recovery_context:
                recovered_instructions += (
                    "\n\nThe prior runtime rollout is unavailable. Continue using only this "
                    "user-visible conversation history; it contains no hidden reasoning:\n" + recovery_context
                )
            self._manager._start_event(profile, "thread_start_requested")
            thread = await codex.thread_start(
                cwd=str(self._manager._paths(profile)[1]),
                developer_instructions=recovered_instructions,
                model=profile.model_id,
                config={"model_reasoning_effort": profile.reasoning_effort},
                model_provider=profile.model_provider_id,
                sandbox=self._sandbox(profile.sandbox),
            )
            self._manager._start_event(profile, "thread_started")
        self._profiles[profile.id] = profile
        self._threads[thread.id] = thread
        return RuntimeSession(thread_id=thread.id, profile_id=profile.id)

    async def run_turn(self, session: RuntimeSession, message: str) -> RuntimeTurn:
        profile = self._profiles[session.profile_id]
        thread = self._threads.get(session.thread_id)
        if thread is None:
            codex = await self._manager.get(profile)
            thread = await codex.thread_resume(session.thread_id, sandbox=self._sandbox(profile.sandbox))
            self._threads[session.thread_id] = thread
        lifecycle_events: list[dict] = [{"event": "turn_started"}]
        result = await thread.run(message, sandbox=self._sandbox(profile.sandbox))
        usage = result.usage
        mcp_calls: list[dict] = []
        for wrapped_item in result.items:
            item = getattr(wrapped_item, "root", wrapped_item)
            # Only structured tool observations are retained. Reasoning items and
            # their hidden content are intentionally excluded from the trace.
            tool = getattr(item, "tool", None)
            server = getattr(item, "server", None)
            if tool and server:
                raw_tool_status = getattr(item, "status", "completed")
                # The SDK currently exposes a string such as
                # ``McpToolCallStatus.completed`` for some providers.  Store a
                # stable status vocabulary in the product trace.
                status = str(getattr(raw_tool_status, "value", raw_tool_status)).rsplit(".", 1)[-1].lower()
                arguments = getattr(item, "arguments", None)
                tool_result = getattr(item, "result", None)
                call = {
                    "server": server,
                    "tool": tool,
                    "input_summary": self._summary(arguments),
                    "output_summary": self._summary(tool_result),
                    "status": status,
                    "duration_ms": getattr(item, "duration_ms", None),
                    "error": self._summary(getattr(item, "error", None)),
                }
                if tool == "knowledge_search":
                    call["retrieval_observation"] = self._retrieval_observation(arguments, tool_result)
                mcp_calls.append(call)
                lifecycle_events.extend(({"event": "tool_started", "tool": tool}, {"event": "tool_completed", "tool": tool, "status": status}))
        text = result.final_response or ""
        if mcp_calls:
            lifecycle_events.append({"event": "model_resumed"})
        if text.strip():
            lifecycle_events.append({"event": "final_response_received", "length": len(text)})
        raw_status = getattr(getattr(result, "status", None), "value", str(getattr(result, "status", "completed")))
        lifecycle_events.append({"event": "turn_completed", "status": raw_status, "has_final_response": bool(text.strip())})
        return RuntimeTurn(
            thread_id=session.thread_id,
            text=text,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
            latency_ms=getattr(result, "duration_ms", None),
            mcp_calls=tuple(mcp_calls),
            status=raw_status,
            error=self._summary(getattr(result, "error", None)),
            lifecycle_events=tuple(lifecycle_events),
        )

    @staticmethod
    def _summary(value: object, limit: int = 600) -> str | None:
        if value is None:
            return None
        text = str(value).replace("\n", " ")
        text = re.sub(r"Incorrect API key provided:\s*[^,.]+", "API key rejected", text, flags=re.IGNORECASE)
        return text if len(text) <= limit else text[: limit - 1] + "…"

    @staticmethod
    def _json_value(value: object) -> object | None:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return None
        return None

    @classmethod
    def _retrieval_observation(cls, arguments: object, result: object) -> dict:
        """Persist minimal retrieval evidence without retaining query or chunk text."""
        argument_value = cls._json_value(arguments)
        query = argument_value.get("query") if isinstance(argument_value, dict) else None
        result_value = cls._json_value(result)
        records = result_value if isinstance(result_value, list) else []
        observations = []
        for item in records[:10]:
            if not isinstance(item, dict):
                continue
            observations.append(
                {
                    "chunk_id": item.get("chunk_id", item.get("id")),
                    "file_id": item.get("file_id"),
                    "score": item.get("score"),
                    "accepted": item.get("accepted"),
                    "rejection_reason": item.get("rejection_reason"),
                }
            )
        return {
            "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest() if isinstance(query, str) else None,
            "query_length": len(query) if isinstance(query, str) else None,
            "result_count": len(records) if isinstance(result_value, list) else None,
            "results": observations,
        }

    async def close(self) -> None:
        await self._manager.close()
