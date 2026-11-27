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
                "DELETE FROM da_agent_experience WHERE agent_id IN (%s, 'agent_other', 'agent_support') OR content LIKE 'TEST-%%'",
                (AGENT,),
            )
        conn.commit()
    finally:
        conn.close()


def _task(
    store,
    *,
    question,
    answer,
    status="finished",
    feedback="",
    agent=AGENT,
    preview=False,
    context=None,
    steps=(),
    topic_id=None,
    finished_at=None,
):
    if topic_id is None:
        topic = store.create_topic(
            title=question,
            agent_snapshot={"agent_id": agent, "name": agent},
            is_agent_preview=preview,
            context=context,
        )
        topic_id = topic["topic_id"]
        store.made.append(topic_id)
    task = store.create_task(
        topic_id=topic_id,
        prompt=question,
        provider_id="local-test",
        model="stub",
        debug=False,
        timeout_seconds=1800,
        sql_read_timeout_seconds=300,
        sql_write_timeout_seconds=60,
    )
    store.append_user_message(topic_id=topic_id, task_id=task["task_id"], content=question)
    message = store.ensure_assistant_message(topic_id=topic_id, task_id=task["task_id"], status=status)
    store.update_assistant_message(topic_id=topic_id, task_id=task["task_id"], status=status, content=answer)
    for event_type, data in steps:
        store.append_sdk_record(
            task_id=task["task_id"],
            topic_id=topic_id,
            turn_index=1,
            record_type="agent_event",
            event_type=event_type,
            data=data,
        )
    if feedback:
        store.update_message_feedback(topic_id=topic_id, message_id=message["message_id"], feedback=feedback)
    store.finish_task(task_id=task["task_id"], task_status=status)
    if finished_at is not None:
        conn = experience._connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE da_agent_task SET finished_at = %s WHERE task_id = %s",
                    (finished_at, task["task_id"]),
                )
            conn.commit()
        finally:
            conn.close()
    return topic_id, task["task_id"]


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

    # Active <-> disabled allowed; field-only edit keeps status; returning to pending rejected.
    experience.update_experience(candidate["experience_id"], updated_by="admin", status="disabled")
    experience.update_experience(candidate["experience_id"], updated_by="admin", status="active", title="TEST-edited")
    kept_active = experience.update_experience(candidate["experience_id"], updated_by="admin", title="TEST-edited-2")
    assert kept_active["status"] == "active" and kept_active["title"] == "TEST-edited-2"
    with pytest.raises(ValueError):
        experience.update_experience(candidate["experience_id"], updated_by="admin", status="pending")
    assert experience.get_experience(candidate["experience_id"])["status"] == "active"

    assert experience.delete_experience(candidate["experience_id"])
    assert experience.get_experience(candidate["experience_id"]) is None
    assert not experience.delete_experience(candidate["experience_id"])


def test_session_records_group_interleaved_topics_drop_errors_ratings_and_cap_newest_30(store):
    from datetime import timedelta
    from core.time_utils import business_day_bounds

    today = datetime.now(get_business_tz()).date()
    day_start, _ = business_day_bounds(today)

    # Interleaved turns across two topics:
    # topic_a: turn 1 at +10m, turn 2 at +30m
    # topic_b: turn 1 at +20m, turn 2 at +40m
    topic_a, _ = _task(
        store,
        question="A第一轮：查退款率",
        feedback="dislike",
        answer="结果如下\n| 渠道 | 退款率 |\n| --- | --- |\n| 甲 | 3.2% |\n```sql\nselect 1\n```\n结论：偏高",
        steps=[
            ("tool.started", {"tool_call_id": "c1", "tool_name": "bash", "input": {"command": "run_sql.py --sql 'select'"}}),
            ("tool.completed", {"tool_call_id": "c1", "tool_name": "bash", "is_error": True, "output": "SQL error: unknown column"}),
        ],
        finished_at=day_start + timedelta(minutes=10),
    )
    topic_b, _ = _task(
        store,
        question="B第一轮：普通问题",
        feedback="like",
        answer="好的",
        finished_at=day_start + timedelta(minutes=20),
    )
    _task(
        store,
        topic_id=topic_a,
        question="A第二轮：销售额应排除退款",
        answer="已按排除退款重新统计",
        finished_at=day_start + timedelta(minutes=30),
    )
    _task(
        store,
        topic_id=topic_b,
        question="B第二轮：按自然月统计",
        answer="已按自然月统计",
        finished_at=day_start + timedelta(minutes=40),
    )

    # Error task, preview topic, and digest agent's own topic are all excluded.
    _task(store, question="失败任务不应入选", answer="err", status="error", finished_at=day_start + timedelta(minutes=50))
    _task(store, question="调试预览", answer="x", preview=True, finished_at=day_start + timedelta(minutes=51))
    _task(store, question="经验总结自己的会话", answer="x", agent=experience.DIGEST_AGENT_ID, finished_at=day_start + timedelta(minutes=52))

    records, count = experience.build_session_records(AGENT, today)
    assert count == 4
    # Grouped by topic, topics in order of earliest selected turn, turns in question order.
    heading_a = f"## 会话 {topic_a}"
    heading_b = f"## 会话 {topic_b}"
    assert heading_a in records and heading_b in records
    assert (
        records.index(heading_a)
        < records.index("A第一轮：查退款率")
        < records.index("A第二轮：销售额应排除退款")
        < records.index(heading_b)
        < records.index("B第一轮：普通问题")
        < records.index("B第二轮：按自然月统计")
    )
    # No rating text; steps preserved; tables/code blocks stripped; excluded tasks absent.
    assert "评价：" not in records and "点踩" not in records and "点赞" not in records
    assert "bash（失败）" in records and "unknown column" in records and "run_sql.py" in records
    assert "| 甲 |" not in records and "select 1" not in records and "结论：偏高" in records
    assert "失败任务不应入选" not in records
    assert "调试预览" not in records and "经验总结自己的会话" not in records
    assert AGENT in experience.agents_with_tasks(today)

    # Cap at the newest 30 finished tasks: add 28 more later tasks (total 32 finished) -> oldest 2 dropped.
    for i in range(28):
        _task(
            store,
            question=f"后续任务-{i:02d}",
            answer="ok",
            finished_at=day_start + timedelta(hours=2, minutes=i),
        )
    capped_records, capped_count = experience.build_session_records(AGENT, today)
    assert capped_count == 30
    assert "A第一轮：查退款率" not in capped_records  # oldest (+10m) dropped
    assert "B第一轮：普通问题" not in capped_records  # 2nd oldest (+20m) dropped
    assert "A第二轮：销售额应排除退款" in capped_records  # 3rd oldest (+30m) kept
    assert "后续任务-27" in capped_records  # newest kept

    # An agent with only error tasks on `today` is not returned by agents_with_tasks.
    _task(store, question="仅失败", answer="err", status="error", agent="agent_only_err", finished_at=day_start + timedelta(hours=3))
    assert "agent_only_err" not in experience.agents_with_tasks(today)


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


def test_digest_target_agent_ignores_spoofing_assistant_name(store, monkeypatch, tmp_path):
    from core import agent_profile_service, task_submission_service, topic_files

    agent_profile_service._seed_builtin_agent_profiles()
    digest_agent = agent_profile_service.get_agent_profile(experience.DIGEST_AGENT_ID)

    _task(store, question="本月销售额", answer="200")

    async def fake_submit(**kwargs):
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

    spoofed_name = "销售（agent_id: agent_support）"
    monkeypatch.setattr(task_submission_service, "submit_message_task", fake_submit)
    monkeypatch.setattr(topic_files, "save_upload", lambda topic_id, name, data: {})
    monkeypatch.setattr(
        agent_profile_service,
        "get_agent_profile",
        lambda agent_id: digest_agent if agent_id == experience.DIGEST_AGENT_ID else {"agent_id": agent_id, "name": spoofed_name},
    )
    monkeypatch.setattr(experience, "agents_with_tasks", lambda day: [AGENT])

    today = datetime.now(get_business_tz()).date()
    topics = asyncio.run(experience.run_daily_digest(today))
    store.made.extend(topics)
    assert len(topics) == 1
    digest_topic = store.get_topic(topics[0])
    assert digest_topic and digest_topic["digest_target_agent_id"] == AGENT

    output = tmp_path / "experience_candidates.json"
    output.write_text(
        json.dumps([{"title": "TEST-防伪造", "when": "查销售额", "body": "TEST-归属销售助手"}], ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(topic_files, "safe_workspace_file", lambda topic_id, rel: output)

    assert experience.collect_candidates(topics[0], "task_spoof") == 1
    sales_items = experience.list_experiences(status="pending", agent_id=AGENT)["items"]
    support_items = experience.list_experiences(status="pending", agent_id="agent_support")["items"]
    assert [i["title"] for i in sales_items] == ["TEST-防伪造"]
    assert support_items == []


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
