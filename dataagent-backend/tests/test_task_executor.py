from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))
from core import task_executor
from core.pi_runtime import PiRunOutcome
from core.task_executor import TaskExecutionInput
from models.schemas import AgentProfileCreateRequest,AgentProfile,AgentCapabilitiesResponse
from core.agent_draft_service import configuration


def test_no_optional_basic_tool_contract():
    assert 'allowed_tools' not in AgentProfileCreateRequest.model_fields
    assert 'allowed_tools' not in AgentProfile.model_fields
    assert 'tools' not in AgentCapabilitiesResponse.model_fields
    assert 'allowed_tools' not in configuration({'allowed_tools':['Bash']})
    assert 'allowed_tools' not in TaskExecutionInput.__dataclass_fields__


PERMISSION_PAYLOAD = {
    "tool_call_id": "call-1",
    "tool_name": "drop_table",
    "tool_title": "删除表",
    "arguments": {"name": "tmp"},
    "annotations": {"readOnlyHint": False, "destructiveHint": True},
    "server_name": "dw",
    "server_identity": "srv1",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,payload", [("permission", PERMISSION_PAYLOAD), ("plan", {"plan": "1. 建表"})])
async def test_executor_forwards_approved_tools_and_builds_permission_card_fields(monkeypatch, tmp_path, kind, payload):
    captured = {}
    committed = []

    class _FakeStore:
        def get_session_checkpoint(self, topic_id):
            return None

        def record_task_system_prompt(self, task_id, system_prompt):
            pass

        def get_topic_approved_tools(self, topic_id):
            assert topic_id == "topic-1"
            return ["srv1:create_table"]

        def commit_session_segment(self, **kwargs):
            committed.append(kwargs)

    async def fake_execute_pi_run(ctx, *, writer, cancel_reason, segment_handler, operation_handler):
        captured["approved_tools"] = ctx.approved_tools
        captured["init_payload"] = ctx.to_init_payload()
        await segment_handler(
            {"header": {}, "entries": [], "leaf_id": None, "turns": 1, "tool_calls": 1},
            {"kind": kind, "request_id": "run1:call-1", "payload": payload},
            0.5,
            None,
        )
        return PiRunOutcome(terminal_status="parked")

    monkeypatch.setattr(task_executor, "get_topic_task_store", lambda: _FakeStore())
    monkeypatch.setattr(task_executor, "resolve_runtime_provider_selection", lambda p, m: {
        "provider_id": "p1", "model": "m1", "api_format": "/v1/messages", "api_key": "k", "base_url": "https://api.example.com"
    })
    monkeypatch.setattr(task_executor, "resolve_enabled_skill_runtime", lambda: {"enabled_roots": {}})
    monkeypatch.setattr(task_executor, "resolve_agent_skill_runtime", lambda s, r: {"enabled_roots": {}, "primary_folder": ""})
    monkeypatch.setattr(task_executor, "prepare_topic_workspace", lambda *a, **kw: tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    monkeypatch.setattr(task_executor, "build_sandbox_config", lambda *a, **kw: ({"runId": "r1"}, tmp_path / "res"))
    monkeypatch.setattr(task_executor, "_build_system_prompt", lambda *a, **kw: "sys")
    monkeypatch.setattr(task_executor, "_build_mcp_servers", lambda *a, **kw: {})
    monkeypatch.setattr(task_executor, "execute_pi_run", fake_execute_pi_run)
    monkeypatch.setattr(task_executor, "get_settings", lambda: SimpleNamespace(
        query_result_limit=100,
        dataagent_timezone="Asia/Shanghai",
        dataagent_experience_injection_enabled=False,
        dataagent_ask_user_question_enabled=True,
        agent_timeout_seconds=1800,
        dataagent_run_idle_timeout_seconds=300,
        agent_max_turns=20,
        agent_max_tool_calls=50,
        agent_bash_timeout_seconds=60,
        max_inline_result_bytes=16384,
        max_context_tokens=128000,
        prompt_cache_retention="short",
    ))
    monkeypatch.setattr(task_executor, "build_governance_settings", lambda *a, **kw: {})

    result = await task_executor.execute_task_stream(TaskExecutionInput(
        task_id="task-1",
        topic_id="topic-1",
        question="删表",
        history=[],
        provider_id="p1",
        model="m1",
        ))

    assert result.task_status == "parked"
    assert captured["approved_tools"] == ["srv1:create_table"]
    assert captured["init_payload"]["approved_tools"] == ["srv1:create_table"]
    assert len(committed) == 1
    card_data = committed[0]["card"]["data"]
    if kind == "plan":
        # Plan cards carry no tool_name in the payload (regression: KeyError 'tool_name').
        assert card_data["tool_name"] == "ExitPlanMode"
        assert card_data["title"] == "请确认执行计划"
        assert card_data["summary"] == "1. 建表"
        assert card_data["destructive"] is False
        return
    assert card_data["title"] == "删除表 需要你确认"
    assert card_data["tool_title"] == "删除表"
    assert card_data["tool_call_id"] == "call-1"
    assert card_data["destructive"] is True
