"""
DataAgent Backend 配置管理
支持环境变量和运行时动态更新
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache

from pydantic import Field, field_validator, model_validator, AliasChoices
from typing import Literal
from pydantic_settings import BaseSettings


@lru_cache(maxsize=1)
def _warn_legacy_sandbox_network() -> None:
    logging.getLogger(__name__).warning(
        "DATAAGENT_SANDBOX_NETWORK is ignored; use DATAAGENT_SANDBOX_NETWORK_ENABLED instead"
    )


class Settings(BaseSettings):
    # ---- 服务 ----
    app_name: str = "dataagent-backend"
    host: str = "0.0.0.0"
    port: int = 8900
    debug: bool = False

    # ---- LLM Provider / Model ----
    llm_provider: str = ""  # anthropic | openrouter | anyrouter | anthropic_compatible
    claude_model: str = ""
    claude_max_tokens: int = 4096
    # One budget tier for every task; an agent's positive max_turns overrides
    # only the turn budget. Card waits are not counted (design 3.5).
    agent_max_turns: int = Field(default=40, gt=0)
    agent_max_tool_calls: int = Field(default=200, gt=0)
    agent_wait_timeout_seconds: int = 20
    agent_timeout_seconds: int = Field(default=1800, gt=0)
    agent_bash_timeout_seconds: int = Field(default=600, gt=0)
    agent_sql_read_timeout_seconds: int = Field(default=300, gt=0)
    agent_sql_write_timeout_seconds: int = Field(default=60, gt=0)
    # Max bytes when buffering a single CLI stdout JSON message before decoding.
    # The SDK default is 1MB; large NL2SQL tool results / partial messages can
    # exceed it and trigger "JSON message exceeded maximum buffer size".
    agent_max_buffer_size_bytes: int = 10 * 1024 * 1024
    followup_suggestions_timeout_seconds: int = 20
    run_events_stream_poll_interval_seconds: int = 1
    run_events_stream_ping_seconds: int = 10
    task_max_concurrency: int = 8
    task_lease_ttl_seconds: int = 30
    task_heartbeat_seconds: int = 5
    task_recovery_scan_interval_seconds: int = 2
    task_recovery_batch_size: int = 20
    schedule_scan_interval_seconds: int = 10
    schedule_scan_batch_size: int = 10
    schedule_lock_ttl_seconds: int = 60

    # ---- Anthropic 兼容认证 ----
    anthropic_api_key: str = ""
    anthropic_auth_token: str = ""
    anthropic_base_url: str = ""

    # ---- MySQL（DataAgent 自身会话存储）----
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "dataagent"
    mysql_password: str = "dataagent123"
    mysql_database: str = "dataagent"
    session_mysql_database: str = Field(default="dataagent", validation_alias=AliasChoices("MYSQL_DATABASE", "SESSION_MYSQL_DATABASE", "DATAAGENT_SESSION_MYSQL_DATABASE"))

    # ---- Redis（task 协调）----
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # ---- Skills ----
    skills_root_dir: str = ""
    skills_output_dir: str = "../.claude/skills/chart-visualization"
    dataagent_upload_max_bytes: int = 20 * 1024 * 1024
    # ---- Agent 交互能力 ----
    # Expose AskUserQuestion cards; write approval gates remain independent.
    dataagent_ask_user_question_enabled: bool = True
    # A question or plan card left unanswered this long is settled by policy:
    # questions take their recommended option (or are skipped), a plan follows
    # the agent's plan_timeout_action. 0 waits for the user indefinitely.
    dataagent_interaction_timeout_seconds: int = Field(default=600, ge=0)

    # Pi is the sole engine; tools execute in a separate sandbox.
    dataagent_runtime_kind: Literal["pi_agent_core"] = "pi_agent_core"
    # Node binary and built Pi Cell entrypoint used when runtime kind is
    # pi_agent_core. Empty values fall back to `node` on PATH and the in-repo
    # dataagent-runtime-pi build output.
    dataagent_node_bin: str = ""
    dataagent_runtime_pi_dir: str = ""
    # Timeout and context governance settings for pi_agent_core runtime.
    # The three context_* names mirror the ``governance_settings`` wire contract
    # consumed by the Cell (src/protocol/frames.ts); keep them in lockstep.
    dataagent_run_idle_timeout_seconds: int = Field(default=300, gt=0)
    dataagent_context_max_inline_result_bytes: int = 16 * 1024
    dataagent_context_protect_tail_turns: int = 6
    dataagent_context_max_context_tokens: int = 64_000
    # Compaction watermarks, as fractions of max_context_tokens. Pruning runs
    # only when the context crosses the high mark, because pi-ai caches the
    # prompt prefix and rewriting history on every turn invalidates it.
    dataagent_context_prune_high_watermark_ratio: float = 0.90
    dataagent_context_prune_target_ratio: float = 0.50
    # Prompt cache retention passed explicitly to the Cell: short | long | off.
    # DISABLE_PROMPT_CACHING never reached the Pi runtime, so caching was on
    # while the backend believed it was off.
    dataagent_pi_cache_retention: str = "short"

    # ---- Topic runtime root / sandbox ----
    # Filesystem root visible to the current process. Containerized backend
    # processes read the shared volume at /dataagent_runtime; local execution
    # leaves this empty and falls back to dataagent_host_root.
    dataagent_runtime_root: str = ""
    # Host-visible persistent root used when asking the host Docker daemon to
    # bind-mount topic subdirectories into sandbox containers.
    dataagent_host_root: str = "/dataagent_runtime"
    dataagent_sandbox_image: str = "oda-sandbox:latest"
    # Per-segment container ceilings so one runaway tool command (fork bomb,
    # huge DataFrame) cannot starve the backend host. Swap equals memory.
    dataagent_sandbox_memory: str = "2g"
    dataagent_sandbox_cpus: str = "2"
    dataagent_sandbox_pids_limit: int = 512
    # Trusted deployments may enable Docker's default bridge network.
    dataagent_sandbox_network_enabled: bool = False
    # Package index for pip installs inside a running sandbox. The image's
    # PIP_INDEX_URL build arg only covers build time; networks that cannot
    # reach PyPI set a mirror here. Empty keeps pip's default.
    dataagent_sandbox_pip_index_url: str = ""
    dataagent_sandbox_pip_trusted_host: str = ""

    # ---- 运行策略 ----
    max_few_shot_examples: int = 5
    max_schema_tables: int = 10
    max_business_rules: int = 5
    query_result_limit: int = 1000

    @model_validator(mode="before")
    @classmethod
    def warn_legacy_sandbox_network(cls, values):
        # Removed fields still appear here when loaded from a dotenv file.
        if any(key.lower() == "dataagent_sandbox_network" for key in os.environ) or (
            isinstance(values, dict) and any(key.lower() == "dataagent_sandbox_network" for key in values)
        ):
            _warn_legacy_sandbox_network()
        return values

    @field_validator("dataagent_sandbox_network_enabled", mode="before")
    @classmethod
    def validate_sandbox_network_enabled(cls, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in {"true", "1", "yes"}:
                return True
            if normalized in {"false", "0", "no"}:
                return False
        if type(value) is int and value in (0, 1):
            return bool(value)
        raise ValueError("DATAAGENT_SANDBOX_NETWORK_ENABLED must be true/false/1/0/yes/no (case-insensitive)")

    @model_validator(mode="after")
    def validate_execution_timeouts(self):
        # Positivity is enforced per field; one command must fit inside the run.
        if self.agent_bash_timeout_seconds >= self.agent_timeout_seconds:
            raise ValueError("agent_bash_timeout_seconds must be less than agent_timeout_seconds")
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"
        populate_by_name = True


_settings = Settings()
_lock = threading.Lock()


def get_settings() -> Settings:
    return _settings


def update_settings(patch: dict) -> Settings:
    """运行时更新配置"""
    global _settings
    with _lock:
        current = _settings.model_dump()
        current.update({k: v for k, v in patch.items() if v is not None})
        _settings = Settings(**current)
    return _settings


def resolve_run_limits(
    cfg: Settings,
    *,
    agent_max_turns: int = 0,
    total_timeout_seconds: int | None = None,
) -> dict[str, int]:
    """Resolve once on the control plane, then carry these values into the sandbox.

    Only max_turns has an agent override; 0 means inherit, never unlimited.
    A persisted task's total budget wins over a later deployment default.
    """
    return {
        "total_timeout_seconds": int(total_timeout_seconds or cfg.agent_timeout_seconds),
        "idle_timeout_seconds": int(cfg.dataagent_run_idle_timeout_seconds),
        "max_turns": int(agent_max_turns or cfg.agent_max_turns),
        "max_tool_calls": int(cfg.agent_max_tool_calls),
        "bash_timeout_seconds": int(cfg.agent_bash_timeout_seconds),
    }


SUPPORTED_RUNTIME_KINDS = ("pi_agent_core",)
DEFAULT_RUNTIME_KIND = "pi_agent_core"


def resolve_runtime_kind(cfg: Settings) -> str:
    return cfg.dataagent_runtime_kind
