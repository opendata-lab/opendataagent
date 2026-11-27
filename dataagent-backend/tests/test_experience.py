"""Experience library against real MySQL; opt in with DATAAGENT_TEST_MYSQL=1."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import experience  # noqa: E402
from core.time_utils import get_business_tz  # noqa: E402
from core.topic_task_store import TopicTaskStore  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")

AGENT = "agent_exp_test"


def _drop_test_digests(store) -> None:
    conn = experience._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic_id FROM da_agent_topic WHERE agent_id = %s AND title LIKE %s",
                (experience.DIGEST_AGENT_ID, "%测试助手%"),
            )
            ids = [row["topic_id"] for row in cur.fetchall() or []]
    finally:
        conn.close()
    for topic_id in ids:
        store.delete_topic(topic_id)


@pytest.fixture
def store():
    store = TopicTaskStore()
    _drop_test_digests(store)
    made: list[str] = []
    store.made = made  # type: ignore[attr-defined]
    yield store
    for topic_id in made:
        store.delete_topic(topic_id)
    _drop_test_digests(store)
    conn = experience._connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM da_agent_experience WHERE agent_id IN (%s, 'agent_other') OR content LIKE 'TEST-%%'",
                (AGENT,),
            )
        conn.commit()
    finally:
        conn.close()


def _task(store, *, question, answer, status="finished", feedback="", agent=AGENT, preview=False, context=None, steps=()):
    topic = store.create_topic(
        title=question,
        agent_snapshot={"agent_id": agent, "name": agent},
        is_agent_preview=preview,
        context=context,
    )
    store.made.append(topic["topic_id"])
    task = store.create_task(
        topic_id=topic["topic_id"],
        prompt=question,
        provider_id="local-test",
        model="stub",
        debug=False,
        timeout_seconds=1800,
        sql_read_timeout_seconds=300,
        sql_write_timeout_seconds=60,
    )
    store.append_user_message(topic_id=topic["topic_id"], task_id=task["task_id"], content=question)
    message = store.ensure_assistant_message(topic_id=topic["topic_id"], task_id=task["task_id"], status=status)
    store.update_assistant_message(topic_id=topic["topic_id"], task_id=task["task_id"], status=status, content=answer)
    for event_type, data in steps:
        store.append_sdk_record(
            task_id=task["task_id"],
            topic_id=topic["topic_id"],
            turn_index=1,
            record_type="agent_event",
            event_type=event_type,
            data=data,
        )
    if feedback:
        store.update_message_feedback(topic_id=topic["topic_id"], message_id=message["message_id"], feedback=feedback)
    store.finish_task(task_id=task["task_id"], task_status=status)
    return topic["topic_id"], task["task_id"]


def test_review_flow_body_search_and_catalog_visibility(store):
    # Creating pending without digest_topic_id is rejected.
    with pytest.raises(ValueError, match="digest_topic_id"):
        experience.create_experience(
            title="TEST-no-digest",
            when_text="关联时",
            scope="agent",
            agent_id=AGENT,
            body="TEST-missing-digest",
            status="pending",
        )

    candidate = experience.create_experience(
        title="TEST-title",
        when_text="关联时",
        scope="agent",
        agent_id=AGENT,
        body="TEST-先确认关联键唯一，正文独有词XYZ99",
        updated_by="digest",
        status="pending",
        digest_topic_id="topic_digest_1",
        source_task_ids=["t1"],
    )
    assert candidate["source"] == "digest"
    assert candidate["status"] == "pending"

    # Pending entries are editable repeatedly while remaining pending.
    edited_pending = experience.update_experience(
        candidate["experience_id"],
        title="TEST-title-v2",
        status="pending",
        updated_by="admin",
    )
    assert edited_pending["title"] == "TEST-title-v2" and edited_pending["status"] == "pending"

    # Pending entry is excluded from active catalog.
    assert experience.build_injection(experience.active_experiences_for(AGENT))["prompt"] == ""

    # Admin approves pending entry -> active.
    experience.update_experience(candidate["experience_id"], updated_by="admin", status="active")

    # Admin-created entry is active immediately with source='manual' and supports long body (20000 chars).
    long_body = "TEST-平台规范-" + ("口" * 19980)
    platform = experience.create_experience(
        title="TEST-global",
        when_text="任何任务",
        scope="global",
        agent_id=AGENT,
        body=long_body,
        updated_by="admin",
    )
    assert platform["agent_id"] is None
    assert platform["status"] == "active"
    assert platform["source"] == "manual"
    assert len(platform["body"]) == len(long_body)

    # Other agent's entry and disabled entry are excluded from catalog.
    experience.create_experience(
        title="TEST-other",
        when_text="任意时",
        scope="agent",
        agent_id="agent_other",
        body="TEST-别的助手",
        updated_by="admin",
    )
    disabled_entry = experience.create_experience(
        title="TEST-disabled",
        when_text="任意时",
        scope="agent",
        agent_id=AGENT,
        body="TEST-已停用",
        updated_by="admin",
    )
    experience.update_experience(disabled_entry["experience_id"], updated_by="admin", status="disabled")
    still_pending = experience.create_experience(
        title="TEST-pending-2",
        when_text="任意时",
        scope="agent",
        agent_id=AGENT,
        body="TEST-仍待审",
        status="pending",
        digest_topic_id="topic_digest_2",
    )
    assert still_pending["status"] == "pending"

    injection = experience.build_injection(experience.active_experiences_for(AGENT))
    section = injection["prompt"]
    assert "TEST-global" in section and "TEST-title-v2" in section
    assert "TEST-other" not in section and "TEST-disabled" not in section and "TEST-pending-2" not in section

    # Keyword search matches body content too.
    by_body = experience.list_experiences(status="active", agent_id=AGENT, keyword="正文独有词XYZ99")
    assert [item["experience_id"] for item in by_body["items"]] == [candidate["experience_id"]]
    assert experience.list_experiences(scope="global", keyword="no-match")["total"] == 0

    # Active <-> disabled allowed, returning to pending rejected.
    experience.update_experience(candidate["experience_id"], updated_by="admin", status="disabled")
    experience.update_experience(candidate["experience_id"], updated_by="admin", status="active", title="TEST-edited")
    with pytest.raises(ValueError):
        experience.update_experience(candidate["experience_id"], updated_by="admin", status="pending")

    assert experience.delete_experience(candidate["experience_id"])
    assert experience.get_experience(candidate["experience_id"]) is None
    assert not experience.delete_experience(candidate["experience_id"])


def test_session_records_include_topic_id_steps_dislikes_first_and_drop_tables(store):
    today = datetime.now(get_business_tz()).date()
    topic1, _ = _task(store, question="普通问题", answer="好的")
    topic2, _ = _task(
        store,
        question="退款率是多少",
        feedback="dislike",
        answer="结果如下\n| 渠道 | 退款率 |\n| --- | --- |\n| 甲 | 3.2% |\n```sql\nselect 1\n```\n结论：偏高",
        steps=[
            ("tool.started", {"tool_call_id": "c1", "tool_name": "bash", "input": {"command": "run_sql.py --sql 'select'"}}),
            ("tool.completed", {"tool_call_id": "c1", "tool_name": "bash", "is_error": True, "output": "SQL error: unknown column"}),
        ],
    )
    _task(store, question="调试预览", answer="x", preview=True)
    _task(store, question="经验总结自己的会话", answer="x", agent=experience.DIGEST_AGENT_ID)

    records, count = experience.build_session_records(AGENT, today)
    assert count == 2
    assert records.index("退款率是多少") < records.index("普通问题")
    assert f"topic_id: {topic1}" in records and f"topic_id: {topic2}" in records
    assert "bash（失败）" in records and "unknown column" in records and "run_sql.py" in records
    assert "| 甲 |" not in records and "select 1" not in records and "结论：偏高" in records
    assert "调试预览" not in records and "经验总结自己的会话" not in records
    assert AGENT in experience.agents_with_tasks(today)


def test_daily_digest_starts_once_per_agent_and_collects_candidates(store, monkeypatch, tmp_path):
    from core import agent_profile_service, task_submission_service, topic_files

    agent_profile_service._seed_builtin_agent_profiles()
    digest_agent = agent_profile_service.get_agent_profile(experience.DIGEST_AGENT_ID)
    assert digest_agent and digest_agent["is_builtin"] and digest_agent["visibility"]["mode"] == "selected"

    _task(store, question="上月销售额", answer="100")
    submitted, uploads = [], []

    async def fake_submit(**kwargs):
        submitted.append(kwargs)
        task = store.create_task(
            topic_id=kwargs["topic_id"],
            prompt=kwargs["message_content"],
            provider_id="local-test",
            model="stub",
            debug=False,
            timeout_seconds=1800,
            sql_read_timeout_seconds=300,
            sql_write_timeout_seconds=60,
        )
        store.append_user_message(topic_id=kwargs["topic_id"], task_id=task["task_id"], content=kwargs["message_content"])
        return {}

    monkeypatch.setattr(task_submission_service, "submit_message_task", fake_submit)
    monkeypatch.setattr(topic_files, "save_upload", lambda topic_id, name, data: uploads.append((topic_id, name, data)) or {})
    monkeypatch.setattr(
        agent_profile_service,
        "get_agent_profile",
        lambda agent_id: digest_agent if agent_id == experience.DIGEST_AGENT_ID else {"agent_id": agent_id, "name": "测试助手"},
    )
    monkeypatch.setattr(experience, "agents_with_tasks", lambda day: [AGENT])

    today = datetime.now(get_business_tz()).date()
    topics = asyncio.run(experience.run_daily_digest(today))
    store.made.extend(topics)
    assert len(topics) == 1 and submitted[0]["agent_id"] == experience.DIGEST_AGENT_ID
    assert "上月销售额" in uploads[0][2].decode()
    assert asyncio.run(experience.run_daily_digest(today)) == []  # same day, same agent: skipped

    output = tmp_path / "experience_candidates.json"
    output.write_text(
        json.dumps(
            [
                {"title": "TEST-title", "when": "测试条件", "body": "TEST-候选一", "source_task_ids": ["a"]},
                {"title": "TEST-title", "when": "测试条件", "body": "", "source_task_ids": []},
                {"title": "TEST-title", "when": "测试条件", "body": "TEST-" + "长" * 600},
                *[{"title": "TEST-title", "when": "测试条件", "body": f"TEST-候选{i}"} for i in range(2, 8)],
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(topic_files, "safe_workspace_file", lambda topic_id, rel: output)

    stored = experience.collect_candidates(topics[0], "task_digest_fake")
    candidates = experience.list_experiences(status="pending", agent_id=AGENT)["items"]
    assert stored == len(candidates) == 3  # five read, two invalid (empty and >500) skipped
    assert {item["digest_topic_id"] for item in candidates} == {topics[0]}
    assert all(item["scope"] == "agent" and item["source"] == "digest" for item in candidates)


def test_follow_up_without_new_output_collects_nothing_and_sources_resolve(store, monkeypatch, tmp_path):
    from core import topic_files

    topic_id, task_id = _task(store, question="来源问题", answer="x")
    output = tmp_path / "experience_candidates.json"
    output.write_text(
        json.dumps([{"title": "TEST-title", "when": "测试条件", "body": "TEST-来源候选", "source_task_ids": [task_id, "task_gone"]}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(topic_files, "safe_workspace_file", lambda topic_id, rel: output)
    monkeypatch.setattr(experience, "_digest_target_agent", lambda topic: AGENT)
    monkeypatch.setattr(store, "get_topic", lambda tid, context=None: {"topic_id": tid, "agent_id": experience.DIGEST_AGENT_ID})
    monkeypatch.setattr(experience, "get_topic_task_store", lambda: store)

    assert experience.collect_candidates("topic_digest", "task_digest") == 1
    output.write_text(
        json.dumps([{"title": "TEST-title", "when": "测试条件", "body": "TEST-来源候选"}, {"title": "TEST-title", "when": "测试条件", "body": "TEST-追问新增"}]),
        encoding="utf-8",
    )
    assert experience.collect_candidates("topic_digest", "task_followup") == 1
    contents = sorted(item["body"] for item in experience.list_experiences(agent_id=AGENT)["items"])
    assert contents == ["TEST-来源候选", "TEST-追问新增"]

    item = next(i for i in experience.list_experiences(agent_id=AGENT)["items"] if i["body"] == "TEST-来源候选")
    [source] = item["sources"]
    assert (source["task_id"], source["topic_id"], source["agent_id"], source["source"]) == (task_id, topic_id, AGENT, "web")
    assert source["question"] == "来源问题" and source["answer"] == "x"


def test_candidates_changed_compares_against_the_pre_task_snapshot(monkeypatch):
    from core import topic_files

    path = experience.CANDIDATES_FILE
    monkeypatch.setattr(topic_files, "snapshot_workspace_state", lambda topic_id: {path: (10, "t2"), "output/a.md": (1, "t1")})
    assert experience.candidates_changed("topic", {}) is True
    assert experience.candidates_changed("topic", {path: (10, "t1")}) is True
    assert experience.candidates_changed("topic", {path: (10, "t2")}) is False
    monkeypatch.setattr(topic_files, "snapshot_workspace_state", lambda topic_id: {})
    assert experience.candidates_changed("topic", {}) is False


def test_one_assistant_failing_does_not_stop_the_others(monkeypatch):
    from core import agent_profile_service

    started = []

    async def fake_start(store, digest_agent, agent_id, day):
        if agent_id == "agent_broken":
            raise RuntimeError("upload failed")
        started.append(agent_id)
        return f"topic_{agent_id}"

    monkeypatch.setattr(agent_profile_service, "get_agent_profile", lambda agent_id: {"agent_id": agent_id})
    monkeypatch.setattr(experience, "agents_with_tasks", lambda day: ["agent_broken", "agent_ok"])
    monkeypatch.setattr(experience, "_start_digest", fake_start)

    assert asyncio.run(experience.run_daily_digest(datetime.now(get_business_tz()).date())) == ["topic_agent_ok"]
