"""Pure selection and runtime mount contracts, independent of model execution."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from core import experience


def entry(id, *, scope="agent", mode="always", body="内容", updated="2026-11-19"):
    return {"experience_id": id, "kind": "knowledge", "scope": scope, "agent_id": "a",
            "status": "active", "title": id, "when_text": "适用时", "body": body,
            "inject_mode": mode, "updated_at": updated, "source_task_ids": []}


def test_shared_budget_global_first_skip_large_entry_and_keep_smaller(caplog):
    items = [entry("agent-short", body="x" * 10), entry("global-first", scope="global", body="g" * 480),
             {**entry("global-too-large", scope="global", body="z" * 500), "kind": "memory"}]
    with caplog.at_level("INFO"):
        result = experience.build_injection(items, log_skips=True)
    assert result["included_ids"] == ["global-too-large", "agent-short"]
    assert result["skipped_ids"] == ["global-first"]
    assert result["used"] == sum(len(experience.entry_text(i)) for i in [items[2], items[0]])
    assert result["used"] <= 1000 < result["total"]
    assert "global-first" in caplog.text and "experience.inject.skipped" in caplog.text


def test_exact_budget_boundary_and_empty_prompt():
    item = entry("one")
    item["body"] = "x" * (1000 - len(experience.entry_text(item)) + len(item["body"]))
    assert experience.build_injection([item])["used"] == 1000
    item["body"] += "x"
    assert experience.build_injection([item])["skipped_ids"] == ["one"]
    assert experience.build_injection([])["prompt"] == ""


def test_index_newest_first_30_and_full_text_only_in_files(monkeypatch, tmp_path):
    items = [entry(f"id{i:02}", mode="on_demand", body=f"unique-body-{i}") for i in range(35)]
    items.append(entry("../../unsafe", mode="on_demand", updated="2026-11-20"))
    prompt, roots = experience.prepare_injection(items, tmp_path / "segment")
    selected = experience.build_injection(items)
    assert selected["indexed_ids"][:2] == ["../../unsafe", "id34"]
    assert len(selected["indexed_ids"]) == 30
    assert "unique-body" not in prompt and "不能覆盖当前用户请求、技能约定和权限规则" in prompt
    assert prompt.count("→") == 30 and "id00（" not in prompt
    assert roots == {experience.INJECTION_FOLDER: str(tmp_path / "segment")}
    assert all(p.parent == tmp_path / "segment" for p in (tmp_path / "segment").glob("*.md"))
    assert len(list((tmp_path / "segment").glob("*.md"))) == 30
    assert "unique-body-34" in "".join(p.read_text() for p in (tmp_path / "segment").glob("*.md"))


def test_body_preserves_paragraphs_and_index_metadata_stays_on_one_line():
    assert experience._clean_text("  第一段\n\n第二段  ", "body", 500) == "第一段\n\n第二段"
    assert experience._clean_text("标题\n下一行", "title", 40) == "标题 下一行"


@pytest.mark.parametrize("changes", [{"title": " "}, {"title": "a" * 41}, {"when_text": "w" * 121},
    {"when_text": ""}, {"body": "b" * 501}, {"scope": "user"}, {"agent_id": None},
    {"inject_mode": "unknown"}, {"kind": "knowledge", "status": "pending"}])
def test_validation_before_database_access(changes, monkeypatch):
    def unexpected(): raise AssertionError("Invalid input reached MySQL")
    monkeypatch.setattr(experience, "_connect", unexpected)
    with pytest.raises(ValueError):
        experience.create_experience(**{**dict(kind="memory", title="title", when_text="when", body="body",
            scope="agent", agent_id="a", updated_by="admin"), **changes})


@pytest.mark.parametrize("enabled,digest,fail", [(True, False, False), (False, False, False), (True, True, False), (True, False, True)])
def test_executor_uses_existing_skill_mount_without_registering_entry_as_skill(monkeypatch, tmp_path, enabled, digest, fail):
    from config import Settings
    from core import task_executor as executor, sandbox_config
    # Fail before invoking a real model, after inspecting the actual run context.
    cfg = Settings(_env_file=None, dataagent_experience_injection_enabled=enabled)
    monkeypatch.setattr(executor, "get_settings", lambda: cfg)
    agent_id = experience.DIGEST_AGENT_ID if digest else "a"
    monkeypatch.setattr(executor, "normalize_agent_snapshot", lambda s: s)
    monkeypatch.setattr(executor, "resolve_runtime_provider_selection", lambda *a: {"provider_id": "p", "model": "m"})
    monkeypatch.setattr(executor, "resolve_enabled_skill_runtime", lambda: {})
    monkeypatch.setattr(executor, "resolve_agent_skill_runtime", lambda *a: {"enabled_roots": {}})
    workspace = tmp_path / "topic" / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(executor, "prepare_topic_workspace", lambda *a, **k: workspace)
    store = SimpleNamespace(get_session_checkpoint=lambda _: None, record_task_system_prompt=lambda *a: None)
    monkeypatch.setattr(executor, "get_topic_task_store", lambda: store)
    monkeypatch.setattr(executor, "active_experiences_for", lambda _: [entry("ondemand", mode="on_demand")])
    monkeypatch.setattr(executor, "_build_system_prompt", lambda *a: "system")
    monkeypatch.setattr(executor, "_build_mcp_servers", lambda *a, **k: {})
    monkeypatch.setattr(executor, "_build_provider_env", lambda *a, **k: {})
    monkeypatch.setattr(executor, "build_governance_settings", lambda *a: {})
    monkeypatch.setattr(sandbox_config.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_config, "_running_in_container", lambda: False)
    monkeypatch.setattr(executor, "PiEventWriter", lambda *a, **k: SimpleNamespace(open_blocks_snapshot=lambda: []))
    seen = []
    async def run(ctx, **kwargs):
        seen.append(ctx)
        if enabled and not digest:
            [mount] = ctx.sandbox["skills"]
            assert mount["folder"] == experience.INJECTION_FOLDER
            assert Path(mount["source"]).parent == workspace.parent
            assert list(Path(mount["source"]).glob("*.md"))
            assert ctx.skills == []  # Only the existing read-only mount, never a discovered skill.
            assert experience.INJECTION_ROOT in ctx.system_prompt
        else:
            assert ctx.sandbox["skills"] == [] and experience.INJECTION_ROOT not in ctx.system_prompt
        if fail: raise RuntimeError("model unavailable")
        return SimpleNamespace(terminal_status="success", answer="ok", error_message="", usage={}, error_code="")
    monkeypatch.setattr(executor, "execute_pi_run", run)
    params = executor.TaskExecutionInput(task_id="task", topic_id="topic", question="q", history=[],
        provider_id="p", model="m", database_hint=None, agent_snapshot={"agent_id": agent_id})
    if fail:
        with pytest.raises(RuntimeError, match="model unavailable"):
            asyncio.run(executor._execute_task_stream(params))
    else:
        asyncio.run(executor._execute_task_stream(params))
    assert len(seen) == 1
    assert not list(workspace.parent.glob("injection-*"))
