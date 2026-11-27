"""MySQL migration tests for 20261126_000043_unify_utc_timestamps (both +08:00 and UTC cases)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pymysql
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, pool

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "20261126_000043_unify_utc_timestamps.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("mig_20261126_000043", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _connect_root():
    try:
        return pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
    except Exception:
        pytest.skip("Local Docker MySQL on 127.0.0.1:3326 not available")


@contextmanager
def _throwaway_schema(schema: str):
    root = _connect_root()
    with root.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        cur.execute(f"CREATE DATABASE `{schema}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    root.close()

    env = os.environ.copy()
    env.update(
        {
            "MYSQL_HOST": "127.0.0.1",
            "MYSQL_PORT": "3326",
            "MYSQL_USER": "root",
            "MYSQL_PASSWORD": "root123",
            "MYSQL_DATABASE": schema,
            "SESSION_MYSQL_DATABASE": schema,
        }
    )
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "20261123_000042"],
            cwd=str(BACKEND_ROOT),
            env=env,
            check=True,
        )
        yield env
    finally:
        cleanup = pymysql.connect(host="127.0.0.1", port=3326, user="root", password="root123", autocommit=True)
        try:
            with cleanup.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        finally:
            cleanup.close()


def _seed_sample_rows(schema: str) -> None:
    """Insert Path-A (+08:00), Path-B (UTC), Path-C (TIMESTAMP), and Path-D rows."""
    conn = pymysql.connect(
        host="127.0.0.1",
        port=3326,
        user="root",
        password="root123",
        database=schema,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        init_command="SET time_zone = '+00:00'",
    )
    try:
        with conn.cursor() as cur:
            # Class A + B on da_user
            cur.execute(
                """INSERT INTO da_user (username, display_name, password_hash, is_active, last_login_at, created_at, updated_at)
                   VALUES ('alice', 'Alice', 'hash', 1, '2026-11-26 10:00:00', '2026-11-26 18:00:00', '2026-11-26 18:05:00')"""
            )
            uid = cur.lastrowid
            # Class B: da_mcp_access_token
            cur.execute(
                """INSERT INTO da_mcp_access_token (token_hash, token_prefix, user_id, name, created_at, last_used_at, revoked_at)
                   VALUES (%s, 'dak_abc', %s, 'tok', '2026-11-26 10:00:00', '2026-11-26 10:15:00', NULL)""",
                ("a" * 64, uid),
            )
            # Class A: da_agent_topic + da_agent_task (nullable cols) + da_agent_message (feedback_at)
            cur.execute(
                """INSERT INTO da_agent_topic
                   (topic_id, title, chat_topic_id, chat_conversation_id, agent_id, source, website_id,
                    external_user_id, visitor_id, auth_user_id, auth_username, is_agent_preview, created_at, updated_at)
                   VALUES ('t1', 'Topic 1', 'ct1', 'cc1', 'agent_default', 'web', '', '', '', '', '', 0,
                           '2026-11-26 18:00:00', '2026-11-26 18:10:00')"""
            )
            cur.execute(
                """INSERT INTO da_agent_task
                   (task_id, topic_id, agent_id, task_status, prompt, provider_id, model_name,
                    cancel_requested_at, started_at, heartbeat_at, finished_at, created_at, updated_at)
                   VALUES ('k1', 't1', 'agent_default', 'finished', 'hi', 'p1', 'm1',
                           NULL, '2026-11-26 18:01:00', '2026-11-26 18:02:00', '2026-11-26 18:03:00',
                           '2026-11-26 18:00:30', '2026-11-26 18:03:00')"""
            )
            cur.execute(
                """INSERT INTO da_agent_message
                   (message_id, topic_id, task_id, sender_type, type, status, content, feedback, feedback_at, created_at, updated_at)
                   VALUES ('m1', 't1', 'k1', 'assistant', 'text', 'success', 'ok', 'like',
                           '2026-11-26 18:20:00', '2026-11-26 18:01:00', '2026-11-26 18:20:00')"""
            )
            # Class A + D on da_agent_sdk_record
            cur.execute(
                """INSERT INTO da_agent_sdk_record
                   (task_id, topic_id, turn_index, record_type, event_type, data, occurred_at, created_at)
                   VALUES ('k1', 't1', 1, 'agent_event', 'turn.started', '{}',
                           '2026-11-26 10:01:00', '2026-11-26 18:01:00')"""
            )
            # Class A + B on da_agent_message_schedule and log
            cur.execute(
                """INSERT INTO da_agent_message_schedule
                   (schedule_id, topic_id, name, message_type, message_content_json, cron_expr, timezone, enabled,
                    last_run_at, next_run_at, created_at, updated_at)
                   VALUES ('s1', 't1', 'Daily', 'text', '"hi"', '0 9 * * *', 'Asia/Shanghai', 1,
                           '2026-11-26 01:00:00', '2026-11-27 01:00:00', '2026-11-26 18:00:00', '2026-11-26 18:00:00')"""
            )
            cur.execute(
                """INSERT INTO da_agent_message_schedule_log
                   (schedule_log_id, schedule_id, status, started_at, finished_at, created_at)
                   VALUES ('sl1', 's1', 'finished', '2026-11-26 10:00:00', '2026-11-26 18:01:00', '2026-11-26 18:00:00')"""
            )
            # Class A + D on da_agent_widget_event & da_model_provider
            cur.execute(
                """INSERT INTO da_agent_widget_event
                   (event_type, source, website_id, external_user_id, visitor_id, client_ts, created_at)
                   VALUES ('widget_open', 'widget', 'site1', '', 'v1', '2026-11-26 10:00:00', '2026-11-26 18:00:00')"""
            )
            cur.execute(
                """INSERT INTO da_model_provider
                   (provider_id, provider_type, api_format, display_name,
                    enabled_models_json, custom_models_json, models_json, model_detections_json,
                    validation_status, validated_at, created_at, updated_at)
                   VALUES ('prov1', 'anthropic_compatible', '/v1/messages', 'P1',
                           '[]', '[]', '[]', '{}',
                           'verified', '2026-11-26 10:00:00', '2026-11-26 18:00:00', '2026-11-26 18:05:00')"""
            )
            # Class C: da_agent_session_checkpoint (TIMESTAMP)
            cur.execute(
                """INSERT INTO da_agent_session_checkpoint
                   (topic_id, task_id, version, header_json, entries_json, state_json, updated_at)
                   VALUES ('t1', 'k1', 1, '{}', '[]', '{}', '2026-11-26 10:00:00')"""
            )
            # Class B + D: eval_dataset and eval_run (untouched by migration)
            cur.execute(
                """INSERT INTO eval_dataset (dataset_id, name, created_at, updated_at)
                   VALUES ('ds1', 'DS', '2026-11-26 10:00:00', '2026-11-26 18:30:00')"""
            )
            cur.execute(
                """INSERT INTO eval_run (run_id, dataset_id, run_status, started_at, ingested_at)
                   VALUES ('r1', 'ds1', 'completed', '2026-11-26 18:00:00', '2026-11-26 10:05:00')"""
            )
    finally:
        conn.close()


def _snapshot(schema: str) -> dict[str, dict]:
    conn = pymysql.connect(
        host="127.0.0.1",
        port=3326,
        user="root",
        password="root123",
        database=schema,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        init_command="SET time_zone = '+00:00'",
    )
    try:
        with conn.cursor() as cur:
            def one(sql: str) -> dict:
                cur.execute(sql)
                row = cur.fetchone() or {}
                return {k: (str(v) if v is not None else None) for k, v in row.items()}

            return {
                "user": one("SELECT created_at, updated_at, last_login_at FROM da_user WHERE username = 'alice'"),
                "token": one("SELECT created_at, last_used_at, revoked_at FROM da_mcp_access_token"),
                "topic": one("SELECT created_at, updated_at FROM da_agent_topic WHERE topic_id = 't1'"),
                "task": one(
                    "SELECT cancel_requested_at, started_at, heartbeat_at, finished_at, created_at, updated_at "
                    "FROM da_agent_task WHERE task_id = 'k1'"
                ),
                "message": one("SELECT feedback_at, created_at, updated_at FROM da_agent_message WHERE message_id = 'm1'"),
                "sdk_record": one("SELECT occurred_at, created_at FROM da_agent_sdk_record WHERE task_id = 'k1'"),
                "schedule": one("SELECT last_run_at, next_run_at, created_at, updated_at FROM da_agent_message_schedule WHERE schedule_id = 's1'"),
                "schedule_log": one("SELECT started_at, finished_at, created_at FROM da_agent_message_schedule_log WHERE schedule_log_id = 'sl1'"),
                "widget_event": one("SELECT client_ts, created_at FROM da_agent_widget_event"),
                "provider": one("SELECT validated_at, created_at, updated_at FROM da_model_provider WHERE provider_id = 'prov1'"),
                "checkpoint": one("SELECT updated_at FROM da_agent_session_checkpoint WHERE topic_id = 't1'"),
                "eval_dataset": one("SELECT created_at, updated_at FROM eval_dataset WHERE dataset_id = 'ds1'"),
                "eval_run": one("SELECT started_at, ingested_at FROM eval_run WHERE run_id = 'r1'"),
            }
    finally:
        conn.close()


def test_migration_shifts_path_a_only_on_plus8_db_and_downgrade_refuses(monkeypatch):
    schema = "tz_test_mig_plus8"
    with _throwaway_schema(schema) as env:
        _seed_sample_rows(schema)
        mig = _load_migration_module()
        engine = create_engine(
            f"mysql+pymysql://root:root123@127.0.0.1:3326/{schema}?charset=utf8mb4",
            poolclass=pool.NullPool,
            connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
        # Run upgrade with +8 offset (28800s) regardless of host MySQL global tz.
        monkeypatch.setattr(mig, "_detect_legacy_offset_seconds", lambda _bind: 28800)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                with ctx.begin_transaction():
                    mig.upgrade()
        engine.dispose()

        snap = _snapshot(schema)
        # Class A shifted -8h; Class B/C/D untouched; ON UPDATE CURRENT_TIMESTAMP did not clobber updated_at.
        assert snap["user"] == {
            "created_at": "2026-11-26 10:00:00",
            "updated_at": "2026-11-26 10:05:00",
            "last_login_at": "2026-11-26 10:00:00",
        }
        assert snap["token"] == {
            "created_at": "2026-11-26 10:00:00",
            "last_used_at": "2026-11-26 10:15:00",
            "revoked_at": None,
        }
        assert snap["topic"] == {
            "created_at": "2026-11-26 10:00:00",
            "updated_at": "2026-11-26 10:10:00",
        }
        assert snap["task"] == {
            "cancel_requested_at": None,
            "started_at": "2026-11-26 10:01:00",
            "heartbeat_at": "2026-11-26 10:02:00",
            "finished_at": "2026-11-26 10:03:00",
            "created_at": "2026-11-26 10:00:30",
            "updated_at": "2026-11-26 10:03:00",
        }
        assert snap["message"] == {
            "feedback_at": "2026-11-26 10:20:00",
            "created_at": "2026-11-26 10:01:00",
            "updated_at": "2026-11-26 10:20:00",
        }
        assert snap["sdk_record"] == {
            "occurred_at": "2026-11-26 10:01:00",
            "created_at": "2026-11-26 10:01:00",
        }
        assert snap["schedule"] == {
            "last_run_at": "2026-11-26 01:00:00",
            "next_run_at": "2026-11-27 01:00:00",
            "created_at": "2026-11-26 10:00:00",
            "updated_at": "2026-11-26 10:00:00",
        }
        assert snap["schedule_log"] == {
            "started_at": "2026-11-26 10:00:00",
            "finished_at": "2026-11-26 10:01:00",
            "created_at": "2026-11-26 10:00:00",
        }
        assert snap["widget_event"] == {
            "client_ts": "2026-11-26 10:00:00",
            "created_at": "2026-11-26 10:00:00",
        }
        assert snap["provider"] == {
            "validated_at": "2026-11-26 10:00:00",
            "created_at": "2026-11-26 10:00:00",
            "updated_at": "2026-11-26 10:05:00",
        }
        assert snap["checkpoint"] == {"updated_at": "2026-11-26 10:00:00"}
        assert snap["eval_dataset"] == {
            "created_at": "2026-11-26 10:00:00",
            "updated_at": "2026-11-26 18:30:00",
        }
        assert snap["eval_run"] == {
            "started_at": "2026-11-26 18:00:00",
            "ingested_at": "2026-11-26 10:05:00",
        }

        with pytest.raises(RuntimeError, match="restore-db.sh"):
            mig.downgrade()


def test_migration_is_noop_on_utc_db(monkeypatch):
    schema = "tz_test_mig_utc"
    with _throwaway_schema(schema):
        _seed_sample_rows(schema)
        before = _snapshot(schema)

        mig = _load_migration_module()
        engine = create_engine(
            f"mysql+pymysql://root:root123@127.0.0.1:3326/{schema}?charset=utf8mb4",
            poolclass=pool.NullPool,
            connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
        monkeypatch.setattr(mig, "_detect_legacy_offset_seconds", lambda _bind: 0)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                with ctx.begin_transaction():
                    mig.upgrade()
        engine.dispose()

        assert _snapshot(schema) == before


def test_detect_legacy_offset_restores_session_utc():
    schema = "tz_test_mig_detect"
    with _throwaway_schema(schema):
        mig = _load_migration_module()
        engine = create_engine(
            f"mysql+pymysql://root:root123@127.0.0.1:3326/{schema}?charset=utf8mb4",
            poolclass=pool.NullPool,
            connect_args={"init_command": "SET time_zone = '+00:00'"},
        )
        with engine.connect() as conn:
            offset = mig._detect_legacy_offset_seconds(conn)
            assert isinstance(offset, int)
            tz_after = conn.exec_driver_sql("SELECT @@session.time_zone").scalar()
            assert tz_after == "+00:00"
        engine.dispose()
