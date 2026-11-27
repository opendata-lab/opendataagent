"""Pure selection, validation, prompt/file, and runtime mount contracts."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from core import experience

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_PRESENTATION_JS = (
    REPO_ROOT
    / "dataagent-frontend"
    / "packages"
    / "agent-conversation"
    / "src"
    / "core"
    / "toolPresentation.js"
)
DIGEST_SKILL_MD = REPO_ROOT / ".claude" / "skills" / "experience-digest" / "SKILL.md"


def entry(id, *, scope="agent", body="内容", when_text="适用场景", updated="2026-11-19T00:00:00Z"):
    return {
        "experience_id": id,
        "scope": scope,
        "agent_id": "a" if scope == "agent" else None,
        "status": "active",
        "title": id,
        "when_text": when_text,
        "body": body,
        "source": "manual",
        "updated_at": updated,
        "source_task_ids": [],
    }


def test_empty_prompt_and_no_mounts(tmp_path):
    result = experience.build_injection([])
    assert result["prompt"] == ""
    assert result["indexed_ids"] == []
    assert result["total_count"] == 0
    assert result["omitted_count"] == 0
    assert result["files"] == {}
    prompt, roots = experience.prepare_injection([], tmp_path / "empty")
    assert prompt == "" and roots == {}


def test_catalog_caps_at_100_while_all_files_written_and_paths_match(tmp_path):
    items = [
        entry(f"id{i:03}", body=f"unique-body-{i}", when_text=f"场景-{i}", updated=f"2026-11-19T00:{i // 60:02d}:{i % 60:02d}Z")
        for i in range(105)
    ]
    items.append(entry("../../unsafe", body="unsafe-body", when_text="路径安全", updated="2026-11-20T00:00:00Z"))

    prompt, roots = experience.prepare_injection(items, tmp_path / "segment")
    selected = experience.build_injection(items)

    assert selected["indexed_ids"][:2] == ["../../unsafe", "id104"]
    assert len(selected["indexed_ids"]) == 100
    assert selected["total_count"] == 106
    assert selected["omitted_count"] == 6
    assert "另有 6 篇未列出" in prompt
    assert "grep" in prompt and "offset/limit" in prompt
    assert "不能覆盖当前用户请求、技能约定和权限规则" in prompt
    assert "unique-body" not in prompt
    assert prompt.count("→") == 100
    assert "id000（" not in prompt
    assert roots == {experience.INJECTION_FOLDER: str(tmp_path / "segment")}

    written_files = list((tmp_path / "segment").glob("*.md"))
    assert len(written_files) == 106
    assert all(p.parent == tmp_path / "segment" for p in written_files)

    # 101st+ entry (omitted from catalog) still has its file written with # title header
    omitted_id = "id000"
    omitted_name = experience.experience_filename(omitted_id)
    omitted_file = tmp_path / "segment" / omitted_name
    assert omitted_file.exists()
    omitted_lines = omitted_file.read_text(encoding="utf-8").splitlines()
    assert omitted_lines[0] == f"# {omitted_id}"
    assert "场景-0" in omitted_lines[1]
    assert "unique-body-0" in omitted_file.read_text(encoding="utf-8")

    # Catalog path matches written filename via the shared path helper
    listed_id = "id104"
    expected_path = experience.experience_file_path(listed_id)
    assert f"- {listed_id}（场景-104）→ {expected_path}" in prompt
    listed_file = tmp_path / "segment" / experience.experience_filename(listed_id)
    assert listed_file.exists()
    assert listed_file.read_text(encoding="utf-8").splitlines()[0] == f"# {listed_id}"


def test_body_preserves_paragraphs_and_index_metadata_stays_on_one_line():
    assert experience._clean_text("  第一段\n\n第二段  ", "body", 20000) == "第一段\n\n第二段"
    assert experience._clean_text("标题\n下一行", "title", 40) == "标题 下一行"
    assert len(experience._clean_text("长" * 20000, "body", 20000)) == 20000


@pytest.mark.parametrize(
    "changes",
    [
        {"title": " "},
        {"title": "a" * 41},
        {"when_text": "w" * 121},
        {"when_text": ""},
        {"body": ""},
        {"body": "b" * 20001},
        {"scope": "user"},
        {"agent_id": None},
        {"status": "pending"},
        {"status": "pending", "digest_topic_id": "   "},
        {"status": "unknown"},
    ],
)
def test_validation_before_database_access(changes, monkeypatch):
    def unexpected():
        raise AssertionError("Invalid input reached MySQL")

    monkeypatch.setattr(experience, "_connect", unexpected)
    with pytest.raises(ValueError):
        experience.create_experience(
            **{
                **dict(
                    title="title",
                    when_text="when",
                    body="body",
                    scope="agent",
                    agent_id="a",
                    updated_by="admin",
                ),
                **changes,
            }
        )


def _fake_db_conn(row_by_id):
    executed = []
    state = {"committed": 0, "rolled_back": 0}

    class _FakeCursor:
        def __init__(self):
            self._row = None

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params=()):
            executed.append((sql, params))
            if "SELECT * FROM da_agent_experience" in sql:
                self._row = row_by_id.get(params[0])

        def fetchone(self):
            return self._row

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def commit(self):
            state["committed"] += 1

        def rollback(self):
            state["rolled_back"] += 1

        def close(self):
            pass

    return _FakeConn, executed, state


def _db_row(item):
    return {
        "experience_id": item["experience_id"],
        "title": item["title"],
        "when_text": item["when_text"],
        "scope": item["scope"],
        "agent_id": item["agent_id"],
        "content": item["body"],
        "source_task_ids_json": "[]",
        "digest_topic_id": item.get("digest_topic_id"),
        "status": item["status"],
        "user_id": None,
        "updated_by": "",
        "created_at": datetime(2026, 11, 19, tzinfo=UTC),
        "updated_at": datetime(2026, 11, 19, tzinfo=UTC),
    }


def test_status_only_patch_locks_row_and_skips_body_validation(monkeypatch):
    existing = {
        **entry("exp1", body="超" * 20005),
        "status": "active",
    }
    conn_cls, executed, state = _fake_db_conn({"exp1": _db_row(existing)})
    monkeypatch.setattr(experience, "_connect", conn_cls)
    monkeypatch.setattr(experience, "get_experience", lambda eid: {**existing, "status": "disabled"})

    # Status-only update locks the row FOR UPDATE and updates only status + audit columns.
    experience.update_experience("exp1", status="disabled", updated_by="admin")
    assert "FOR UPDATE" in executed[0][0]
    update_sql = executed[1][0]
    assert "status = %s" in update_sql
    assert "content = %s" not in update_sql
    assert state["committed"] == 1

    # Explicitly editing body still enforces the 20000-character limit and rolls back.
    with pytest.raises(ValueError, match="20000"):
        experience.update_experience("exp1", body="超" * 20001, updated_by="admin")
    assert state["rolled_back"] == 1


def test_pending_editable_and_stale_patch_cannot_demote_approved_entry(monkeypatch):
    pending = {**entry("exp_p"), "status": "pending", "digest_topic_id": "topic_d"}
    active = {**entry("exp_a"), "status": "active"}
    conn_cls, executed, state = _fake_db_conn({"exp_p": _db_row(pending), "exp_a": _db_row(active)})

    monkeypatch.setattr(experience, "_connect", conn_cls)
    monkeypatch.setattr(experience, "get_experience", lambda eid: pending if eid == "exp_p" else active)

    # Editing a pending item while keeping it pending is allowed.
    assert experience.update_experience("exp_p", title="修订标题", status="pending") is not None
    assert "FOR UPDATE" in executed[0][0]

    # Editing fields on an already-approved item without supplying status never touches status.
    executed.clear()
    assert experience.update_experience("exp_a", title="修订已生效标题") is not None
    assert "status = %s" not in executed[1][0]
    assert "title = %s" in executed[1][0]

    # Returning a reviewed item to pending is rejected and rolled back.
    with pytest.raises(ValueError, match="pending"):
        experience.update_experience("exp_a", status="pending")
    assert state["rolled_back"] == 1


def test_digest_target_agent_uses_only_structured_topic_metadata(monkeypatch):
    def fail_messages(*args, **kwargs):
        raise AssertionError("must not read topic messages to resolve digest target agent")

    monkeypatch.setattr(experience, "get_topic_task_store", lambda: SimpleNamespace(list_topic_messages=fail_messages))
    assert experience._digest_target_agent({"topic_id": "t1", "digest_target_agent_id": "agent_sales"}) == "agent_sales"
    assert experience._digest_target_agent({"topic_id": "t1", "digest_target_agent_id": ""}) is None
    assert experience._digest_target_agent({"topic_id": "t1"}) is None


def test_preview_and_runtime_eligibility_match(monkeypatch):
    sample = [entry("k1", body="口径正文")]
    monkeypatch.setattr(experience, "active_experiences_for", lambda agent_id: sample)
    monkeypatch.setattr(experience, "_attach_sources", lambda items: None)
    monkeypatch.setattr(experience, "list_experiences", lambda **kw: {"total": 1, "items": []})

    disabled = experience.injection_preview("a", injection_enabled=False)
    assert disabled["eligible"] is False
    assert disabled["reason"] == "disabled_by_config"
    assert disabled["prompt"] == ""
    assert disabled["indexed_ids"] == []
    assert disabled["injection_enabled"] is False

    digest = experience.injection_preview(experience.DIGEST_AGENT_ID, injection_enabled=True)
    assert digest["eligible"] is False
    assert digest["reason"] == "digest_agent"
    assert digest["prompt"] == ""
    assert digest["indexed_ids"] == []

    eligible = experience.injection_preview("a", injection_enabled=True)
    assert eligible["eligible"] is True
    assert eligible["reason"] == "eligible"
    assert eligible["prompt"] == experience.build_injection(sample)["prompt"]
    assert eligible["indexed_ids"] == ["k1"]


def test_frontend_knowledge_root_matches_backend_injection_root():
    content = TOOL_PRESENTATION_JS.read_text(encoding="utf-8")
    match = re.search(r"export const KNOWLEDGE_ROOT\s*=\s*['\"]([^'\"]+)['\"]", content)
    assert match is not None, "KNOWLEDGE_ROOT constant not found in toolPresentation.js"
    assert match.group(1) == experience.INJECTION_ROOT


def test_digest_skill_updated_for_knowledge_and_grounded_business_metrics():
    text = DIGEST_SKILL_MD.read_text(encoding="utf-8")
    assert "知识" in text
    assert "topic_id" in text
    assert "不要写：业务口径" not in text
    assert "用户原话" in text
    assert "source_task_ids" in text
    assert "记忆页面" not in experience.digest_agent_payload()["description"]
    assert "知识页面" in experience.digest_agent_payload()["description"]


@pytest.mark.parametrize(
    "enabled,digest,fail",
    [(True, False, False), (False, False, False), (True, True, False), (True, False, True)],
)
def test_executor_uses_existing_skill_mount_without_registering_entry_as_skill(monkeypatch, tmp_path, enabled, digest, fail):
    from config import Settings
    from core import sandbox_config, task_executor as executor

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
    store = SimpleNamespace(
        get_session_checkpoint=lambda _: None,
        record_task_system_prompt=lambda *a: None,
        get_topic_approved_tools=lambda _: [],
    )
    monkeypatch.setattr(executor, "get_topic_task_store", lambda: store)
    sample = [entry("ondemand")]
    monkeypatch.setattr(executor, "active_experiences_for", lambda _: sample)
    monkeypatch.setattr(executor, "_build_system_prompt", lambda *a: "system")
    monkeypatch.setattr(executor, "_build_mcp_servers", lambda *a, **k: {})
    monkeypatch.setattr(executor, "_build_provider_env", lambda *a, **k: {})
    monkeypatch.setattr(executor, "build_governance_settings", lambda *a: {})
    monkeypatch.setattr(sandbox_config.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(sandbox_config, "_running_in_container", lambda: False)
    monkeypatch.setattr(executor, "PiEventWriter", lambda *a, **k: SimpleNamespace(open_blocks_snapshot=lambda: []))

    # Preview eligibility agrees with runtime behavior for all combinations.
    monkeypatch.setattr(experience, "active_experiences_for", lambda _: sample)
    monkeypatch.setattr(experience, "_attach_sources", lambda items: None)
    monkeypatch.setattr(experience, "list_experiences", lambda **kw: {"total": 0, "items": []})
    preview = experience.injection_preview(agent_id, injection_enabled=enabled)

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
            assert preview["prompt"] in ctx.system_prompt and preview["eligible"] is True
        else:
            assert ctx.sandbox["skills"] == [] and experience.INJECTION_ROOT not in ctx.system_prompt
            assert preview["prompt"] == "" and preview["eligible"] is False
        if fail:
            raise RuntimeError("model unavailable")
        return SimpleNamespace(terminal_status="success", answer="ok", error_message="", usage={}, error_code="")

    monkeypatch.setattr(executor, "execute_pi_run", run)
    params = executor.TaskExecutionInput(
        task_id="task",
        topic_id="topic",
        question="q",
        history=[],
        provider_id="p",
        model="m",
        agent_snapshot={"agent_id": agent_id},
    )
    if fail:
        with pytest.raises(RuntimeError, match="model unavailable"):
            asyncio.run(executor._execute_task_stream(params))
    else:
        asyncio.run(executor._execute_task_stream(params))
    assert len(seen) == 1
    assert not list(workspace.parent.glob("injection-*"))
