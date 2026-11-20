"""Experience library against real MySQL; opt in with DATAAGENT_TEST_MYSQL=1."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import experience  # noqa: E402
from core.topic_task_store import TopicTaskStore  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")

AGENT = "agent_exp_test"


def _drop_test_digests(store) -> None:
    conn = experience._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT topic_id FROM da_agent_topic WHERE auth_user_id = %s AND title LIKE %s",
                        (experience.DIGEST_USER_ID, "%测试助手%"))
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
            cur.execute("DELETE FROM da_agent_experience WHERE agent_id IN (%s, 'agent_other') OR content LIKE 'TEST-%%'", (AGENT,))
        conn.commit()
    finally:
        conn.close()


def _task(store, *, question, answer, status="finished", feedback="", agent=AGENT, preview=False, context=None, steps=()):
    topic = store.create_topic(title=question, agent_snapshot={"agent_id": agent, "name": agent},
                               is_agent_preview=preview, context=context)
    store.made.append(topic["topic_id"])
    task = store.create_task(topic_id=topic["topic_id"], prompt=question, provider_id="local-test", model="stub",
        database_hint=None, debug=False, timeout_seconds=1800, sql_read_timeout_seconds=300, sql_write_timeout_seconds=60)
    store.append_user_message(topic_id=topic["topic_id"], task_id=task["task_id"], content=question)
    message = store.ensure_assistant_message(topic_id=topic["topic_id"], task_id=task["task_id"], status=status)
    store.update_assistant_message(topic_id=topic["topic_id"], task_id=task["task_id"], status=status, content=answer)
    for event_type, data in steps:
        store.append_sdk_record(task_id=task["task_id"], topic_id=topic["topic_id"], turn_index=1,
                                record_type="agent_event", event_type=event_type, data=data)
    if feedback:
        store.update_message_feedback(topic_id=topic["topic_id"], message_id=message["message_id"], feedback=feedback)
    store.finish_task(task_id=task["task_id"], task_status=status)
    return topic["topic_id"], task["task_id"]


def test_review_flow_and_prompt_section(store):
    candidate = experience.create_experience(scope="assistant", agent_id=AGENT, content="TEST-先确认关联键唯一",
                                             updated_by="digest", status="candidate", source_task_ids=["t1"])
    assert experience.experience_prompt_section(AGENT) == ""  # candidates never reach prompts

    experience.update_experience(candidate["experience_id"], updated_by="admin", status="active")
    platform = experience.create_experience(scope="platform", agent_id=None, content="TEST-平台规范", updated_by="admin")
    experience.create_experience(scope="assistant", agent_id="agent_other", content="TEST-别的助手", updated_by="admin")

    assert experience.active_experiences_for(AGENT)[:2] == ["TEST-平台规范", "TEST-先确认关联键唯一"]
    section = experience.experience_prompt_section(AGENT)
    assert "# 历史经验参考" in section and "TEST-别的助手" not in section

    experience.update_experience(platform["experience_id"], updated_by="admin", status="retired")
    assert "TEST-平台规范" not in experience.experience_prompt_section(AGENT)
    listed = experience.list_experiences(status="active", agent_id=AGENT)
    assert [item["content"] for item in listed["items"]] == ["TEST-先确认关联键唯一"]
    assert listed["items"][0]["updated_by"] == "admin"

    with pytest.raises(ValueError):
        experience.create_experience(scope="assistant", agent_id=None, content="TEST-x", updated_by="admin")
    with pytest.raises(ValueError):
        experience.create_experience(scope="platform", agent_id=None, content="x" * 501, updated_by="admin")


def test_session_records_include_steps_put_dislikes_first_and_drop_tables(store):
    today = experience.db_now().date()
    _task(store, question="普通问题", answer="好的")
    _task(store, question="退款率是多少", feedback="dislike",
          answer="结果如下\n| 渠道 | 退款率 |\n| --- | --- |\n| 甲 | 3.2% |\n```sql\nselect 1\n```\n结论：偏高",
          steps=[("tool.started", {"tool_call_id": "c1", "tool_name": "bash", "input": {"command": "run_sql.py --sql 'select'"}}),
                 ("tool.completed", {"tool_call_id": "c1", "tool_name": "bash", "is_error": True, "output": "SQL error: unknown column"})])
    _task(store, question="调试预览", answer="x", preview=True)
    _task(store, question="经验总结自己的会话", answer="x", context={"source": "web", "auth_user_id": experience.DIGEST_USER_ID})

    records, count = experience.build_session_records(AGENT, today)
    assert count == 2
    assert records.index("退款率是多少") < records.index("普通问题")
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
        task = store.create_task(topic_id=kwargs["topic_id"], prompt=kwargs["message_content"], provider_id="local-test",
            model="stub", database_hint=None, debug=False, timeout_seconds=1800, sql_read_timeout_seconds=300,
            sql_write_timeout_seconds=60)
        store.append_user_message(topic_id=kwargs["topic_id"], task_id=task["task_id"], content=kwargs["message_content"])
        return {}

    monkeypatch.setattr(task_submission_service, "submit_message_task", fake_submit)
    monkeypatch.setattr(topic_files, "save_upload", lambda topic_id, name, data: uploads.append((topic_id, name, data)) or {})
    monkeypatch.setattr(agent_profile_service, "get_agent_profile",
                        lambda agent_id: digest_agent if agent_id == experience.DIGEST_AGENT_ID else {"agent_id": agent_id, "name": "测试助手"})
    monkeypatch.setattr(experience, "agents_with_tasks", lambda day: [AGENT])

    topics = asyncio.run(experience.run_daily_digest(experience.db_now().date()))
    store.made.extend(topics)
    assert len(topics) == 1 and submitted[0]["agent_id"] == experience.DIGEST_AGENT_ID
    assert "上月销售额" in uploads[0][2].decode()
    assert asyncio.run(experience.run_daily_digest(experience.db_now().date())) == []  # same day, same agent: skipped

    output = tmp_path / "experience_candidates.json"
    output.write_text(json.dumps([
        {"content": "TEST-候选一", "source_task_ids": ["a"]},
        {"content": "", "source_task_ids": []},
        {"content": "TEST-" + "长" * 600},
        *[{"content": f"TEST-候选{i}"} for i in range(2, 8)],
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(topic_files, "safe_workspace_file", lambda topic_id, rel: output)

    stored = experience.collect_candidates(topics[0], "task_digest_fake")
    candidates = experience.list_experiences(status="candidate", agent_id=AGENT)["items"]
    assert stored == len(candidates) == 3  # five read, two invalid skipped
    assert {item["digest_topic_id"] for item in candidates} == {topics[0]}
    assert all(item["scope"] == "assistant" for item in candidates)


def test_follow_up_without_new_output_collects_nothing_and_sources_resolve(store, monkeypatch, tmp_path):
    from core import topic_files

    topic_id, task_id = _task(store, question="来源问题", answer="x")
    output = tmp_path / "experience_candidates.json"
    output.write_text(json.dumps([{"content": "TEST-来源候选", "source_task_ids": [task_id, "task_gone"]}]), encoding="utf-8")
    monkeypatch.setattr(topic_files, "safe_workspace_file", lambda topic_id, rel: output)
    monkeypatch.setattr(experience, "_digest_target_agent", lambda topic: AGENT)
    monkeypatch.setattr(store, "get_topic", lambda tid, context=None: {"topic_id": tid, "agent_id": experience.DIGEST_AGENT_ID})
    monkeypatch.setattr(experience, "get_topic_task_store", lambda: store)

    # A follow-up in the digest conversation that did not rewrite the file.
    assert experience.collect_candidates("topic_digest", "task_followup", ["output/answer.md"]) == 0
    assert experience.collect_candidates("topic_digest", "task_digest", [experience.CANDIDATES_FILE]) == 1

    item = experience.list_experiences(status="candidate", agent_id=AGENT)["items"][0]
    assert item["sources"] == [{"task_id": task_id, "topic_id": topic_id, "agent_id": AGENT, "source": "web"}]


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
    assert asyncio.run(experience.run_daily_digest(experience.db_now().date())) == ["topic_agent_ok"]
