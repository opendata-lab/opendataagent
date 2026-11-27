"""MySQL boundary tests for core.db.connect_mysql UTC converter, RuntimeRegistryStore, and EvalStore."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pymysql
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import get_settings, update_settings
from core.db import connect_mysql
from core.eval_store import EvalStore
from core.runtime_registry_store import RuntimeRegistryStore


def _connect_root():
    try:
        return pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
    except Exception:
        pytest.skip("Local Docker MySQL on 127.0.0.1:3326 not available")


@pytest.fixture
def temp_mysql_schema():
    schema = "tz_test_db_conv"
    root = _connect_root()
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    root.close()

    prev = get_settings().model_dump()
    update_settings(
        {
            "mysql_host": "127.0.0.1",
            "mysql_port": 3326,
            "mysql_user": "root",
            "mysql_password": "root123",
            "session_mysql_database": schema,
        }
    )
    try:
        yield schema
    finally:
        update_settings(prev)
        cleanup = pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
        try:
            with cleanup.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        finally:
            cleanup.close()


def test_connect_mysql_utc_session_and_aware_converter(temp_mysql_schema):
    conn = connect_mysql(database=temp_mysql_schema, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT @@session.time_zone AS tz")
            assert cur.fetchone()["tz"] == "+00:00"

            cur.execute(
                """CREATE TABLE tz_probe (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    dt DATETIME NOT NULL,
                    ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

            # Naive datetime must raise TypeError on write.
            with pytest.raises(TypeError, match="Naive datetime"):
                cur.execute("INSERT INTO tz_probe (dt) VALUES (%s)", (datetime(2026, 11, 26, 9, 0, 0),))

            # Aware non-UTC (+08:00) converts to UTC on write and reads back as aware UTC.
            shanghai_dt = datetime(2026, 11, 26, 17, 30, 45, tzinfo=timezone(timedelta(hours=8)))
            cur.execute("INSERT INTO tz_probe (dt, ts) VALUES (%s, %s)", (shanghai_dt, shanghai_dt))

            cur.execute("SELECT dt, ts, CAST(dt AS CHAR) AS raw_dt FROM tz_probe LIMIT 1")
            row = cur.fetchone()
            assert row["raw_dt"] == "2026-11-26 09:30:45"
            assert row["dt"] == datetime(2026, 11, 26, 9, 30, 45, tzinfo=UTC)
            assert row["dt"].tzinfo == UTC
            assert row["ts"] == datetime(2026, 11, 26, 9, 30, 45, tzinfo=UTC)
            assert row["ts"].tzinfo == UTC
            assert row["dt"] == shanghai_dt
    finally:
        conn.close()


def test_runtime_registry_and_eval_store_roundtrip_utc_rfc3339(temp_mysql_schema):
    env = os.environ.copy()
    env.update(
        {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3326",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "root123",
            "MYSQL_DATABASE": temp_mysql_schema,
            "SESSION_MYSQL_DATABASE": temp_mysql_schema,
        }
    )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(BACKEND_ROOT), env=env, check=True)

    # RuntimeRegistryStore: saving RFC 3339 Z validated_at must not trigger MySQL 1292.
    registry = RuntimeRegistryStore()
    saved = registry.save_provider(
        {
            "provider_id": "test_provider",
            "provider_type": "anthropic_compatible",
            "api_format": "/v1/messages",
            "display_name": "Test Provider",
            "validation_status": "verified",
            "validated_at": "2026-11-26T09:15:00Z",
        }
    )
    assert saved["validated_at"] == "2026-11-26T09:15:00Z"
    assert saved["created_at"].endswith("Z")
    assert saved["updated_at"].endswith("Z")
    # Re-saving the serialized payload (idempotent round-trip) must also succeed.
    resaved = registry.save_provider(saved)
    assert resaved["validated_at"] == "2026-11-26T09:15:00Z"

    # EvalStore: writes aware UTC and serializes all timestamps as Z.
    eval_store = EvalStore()
    ds = eval_store.create_dataset("ds-tz-1", "TZ Dataset")
    assert ds["created_at"].endswith("Z")
    assert ds["updated_at"].endswith("Z")

    run_aware = eval_store.ingest_run(
        {"dataset_id": "ds-tz-1", "run_label": "20261126-170000", "run_status": "completed", "evaluation_engine": "builtin"},
        [],
        run_id="run-aware-1",
        started_at="2026-11-26T17:00:00+08:00",
    )
    assert run_aware["started_at"] == "2026-11-26T09:00:00Z"
    assert run_aware["ingested_at"].endswith("Z")

    # Naive or omitted started_at stays None (no run_label guessing).
    run_naive = eval_store.ingest_run(
        {"dataset_id": "ds-tz-1", "run_label": "20261126-170000", "run_status": "completed", "evaluation_engine": "builtin"},
        [],
        run_id="run-naive-1",
        started_at="2026-11-26 17:00:00",
    )
    assert run_naive["started_at"] is None
    assert run_naive["ingested_at"].endswith("Z")

    trends = eval_store.trend_series(dataset_id="ds-tz-1")
    assert len(trends) == 2
    assert all(item["time"].endswith("Z") for item in trends)


def test_admin_filters_and_experience_bounds_against_real_mysql(temp_mysql_schema, monkeypatch):
    from datetime import date
    from core import experience
    from core.topic_task_store import TopicTaskStore

    env = os.environ.copy()
    env.update(
        {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3326",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "root123",
            "MYSQL_DATABASE": temp_mysql_schema,
            "SESSION_MYSQL_DATABASE": temp_mysql_schema,
        }
    )
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=str(BACKEND_ROOT), env=env, check=True)

    # Seed topics/tasks/messages with exact UTC timestamps around Shanghai midnight:
    # 2026-11-25T15:59:59Z = 2026-11-25 23:59:59+08:00 (day 25)
    # 2026-11-25T16:00:00Z = 2026-11-26 00:00:00+08:00 (day 26 start)
    # 2026-11-26T16:00:00Z = 2026-11-27 00:00:00+08:00 (day 26 exclusive end -> day 27)
    conn = connect_mysql(database=temp_mysql_schema, autocommit=True)
    try:
        with conn.cursor() as cur:
            for tid, kid, mid, ts in (
                ("t_before", "k_before", "m_before", datetime(2026, 11, 25, 15, 59, 59, tzinfo=UTC)),
                ("t_inside", "k_inside", "m_inside", datetime(2026, 11, 25, 16, 0, 0, tzinfo=UTC)),
                ("t_after", "k_after", "m_after", datetime(2026, 11, 26, 16, 0, 0, tzinfo=UTC)),
            ):
                cur.execute(
                    """INSERT INTO da_agent_topic
                       (topic_id, title, chat_topic_id, chat_conversation_id, agent_id, source, website_id,
                        external_user_id, visitor_id, auth_user_id, auth_username, is_agent_preview, created_at, updated_at)
                       VALUES (%s, %s, %s, %s, 'agent_tz_test', 'web', '', '', '', '', '', 0, %s, %s)""",
                    (tid, tid, f"c_{tid}", f"cc_{tid}", ts, ts),
                )
                cur.execute(
                    """INSERT INTO da_agent_task
                       (task_id, topic_id, agent_id, task_status, prompt, provider_id, model_name,
                        finished_at, created_at, updated_at)
                       VALUES (%s, %s, 'agent_tz_test', 'finished', 'q', 'p', 'm', %s, %s, %s)""",
                    (kid, tid, ts, ts, ts),
                )
                cur.execute(
                    """INSERT INTO da_agent_message
                       (message_id, topic_id, task_id, sender_type, type, status, content, feedback, feedback_at, show_in_ui, created_at, updated_at)
                       VALUES (%s, %s, %s, 'assistant', 'text', 'success', 'ans', 'like', %s, 1, %s, %s)""",
                    (mid, tid, kid, ts, ts, ts),
                )
    finally:
        conn.close()

    store = TopicTaskStore()
    start = datetime(2026, 11, 26, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    end = datetime(2026, 11, 27, 0, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    topics = store.admin_list_topics(agent_id="agent_tz_test", start=start, end=end)
    assert [t["topic_id"] for t in topics["items"]] == ["t_inside"]
    assert topics["items"][0]["created_at"] == "2026-11-25T16:00:00Z"

    feedback = store.list_message_feedback(agent_id="agent_tz_test", start=start, end=end)
    assert [f["message_id"] for f in feedback["items"]] == ["m_inside"]
    assert feedback["items"][0]["feedback_at"] == "2026-11-25T16:00:00Z"

    # Experience daily bounds use DATAAGENT_TIMEZONE (Asia/Shanghai).
    monkeypatch.setattr(experience, "get_topic_task_store", lambda: store)
    assert "agent_tz_test" in experience.agents_with_tasks(date(2026, 11, 26))
    markdown, count = experience.build_session_records("agent_tz_test", date(2026, 11, 26))
    assert count == 1
    assert "k_inside" in markdown
    assert "k_before" not in markdown
    assert "k_after" not in markdown
