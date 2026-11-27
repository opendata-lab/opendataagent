"""Migration tests for 20261127_000047_knowledge_only (upgrade + idempotency + downgrade)."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pymysql
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = BACKEND_ROOT / "alembic" / "versions" / "20261127_000047_knowledge_only.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("mig_20261127_000047", MIGRATION_PATH)
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
            [sys.executable, "-m", "alembic", "upgrade", "20261127_000046"],
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


def test_downgrade_raises_restore_backup_error():
    mig = _load_migration_module()
    with pytest.raises(RuntimeError, match="restore-db.sh"):
        mig.downgrade()


def test_upgrade_converts_memory_rows_widens_content_and_is_idempotent():
    schema = "knowledge_test_migration"
    with _throwaway_schema(schema) as env:
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
                cur.execute(
                    """
                    INSERT INTO da_agent_experience
                        (experience_id, kind, title, when_text, inject_mode, scope, agent_id, content,
                         source_task_ids_json, digest_topic_id, status, updated_by)
                    VALUES
                        ('exp_mem_1', 'memory', '待审记忆', '退款时', 'on_demand', 'agent', 'agent_sales',
                         '排除退款订单', '["task_1"]', 'topic_digest_1', 'pending', ''),
                        ('exp_know_1', 'knowledge', '全局知识', '查数前', 'always', 'global', NULL,
                         '先看口径文档', '[]', NULL, 'active', 'admin')
                    """
                )
        finally:
            conn.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "20261127_000047"],
            cwd=str(BACKEND_ROOT),
            env=env,
            check=True,
        )

        # Verify schema and preserved rows (including memory -> pending knowledge).
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
                cur.execute(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_SET_NAME, COLLATION_NAME, IS_NULLABLE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'da_agent_experience'
                    """,
                    (schema,),
                )
                cols = {row["COLUMN_NAME"]: row for row in cur.fetchall()}
                assert "kind" not in cols
                assert "inject_mode" not in cols
                assert cols["content"]["DATA_TYPE"].lower() == "mediumtext"
                assert cols["content"]["CHARACTER_SET_NAME"] == "utf8mb4"
                assert cols["content"]["IS_NULLABLE"] == "NO"

                cur.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'da_agent_topic' AND COLUMN_NAME = 'digest_target_agent_id'
                    """,
                    (schema,),
                )
                assert cur.fetchone() is not None

                cur.execute("SHOW INDEX FROM da_agent_experience")
                indexes = {}
                for row in cur.fetchall():
                    indexes.setdefault(row["Key_name"], []).append((row["Seq_in_index"], row["Column_name"]))
                assert "idx_da_agent_experience_kind" not in indexes
                assert [c for _, c in sorted(indexes["idx_da_agent_experience_status_updated"])] == ["status", "updated_at"]

                cur.execute("SELECT * FROM da_agent_experience ORDER BY experience_id")
                rows = {row["experience_id"]: row for row in cur.fetchall()}
                assert set(rows) == {"exp_mem_1", "exp_know_1"}
                assert rows["exp_mem_1"]["status"] == "pending"
                assert rows["exp_mem_1"]["digest_topic_id"] == "topic_digest_1"
                assert rows["exp_mem_1"]["content"] == "排除退款订单"
                assert rows["exp_know_1"]["status"] == "active"

                # Verify 20000 multi-byte Chinese chars (60000 bytes, ~60KB; or 25000 = 75KB > TEXT 65535B) fit in MEDIUMTEXT.
                big_content = "口" * 25000
                cur.execute(
                    "UPDATE da_agent_experience SET content = %s WHERE experience_id = 'exp_know_1'",
                    (big_content,),
                )
                cur.execute("SELECT CHAR_LENGTH(content) AS n FROM da_agent_experience WHERE experience_id = 'exp_know_1'")
                assert cur.fetchone()["n"] == 25000
        finally:
            conn.close()

        # Re-running upgrade() on an already-migrated schema must be a no-op.
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import create_engine, pool

        mig = _load_migration_module()
        engine = create_engine(
            f"mysql+pymysql://root:root123@127.0.0.1:3326/{schema}?charset=utf8mb4",
            poolclass=pool.NullPool,
        )
        try:
            with engine.connect() as sa_conn:
                ctx = MigrationContext.configure(sa_conn)
                with Operations.context(ctx):
                    mig.upgrade()
        finally:
            engine.dispose()

        # Running alembic downgrade must fail with RuntimeError.
        down = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "20261127_000046"],
            cwd=str(BACKEND_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
        assert down.returncode != 0
        assert "restore-db.sh" in (down.stderr + down.stdout)


def test_upgrade_recovers_from_partial_state_after_kind_dropped():
    """Simulate a mid-migration failure after dropping kind while revision 46 is still stamped."""
    schema = "knowledge_test_migration_partial"
    with _throwaway_schema(schema) as env:
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
                cur.execute(
                    """
                    INSERT INTO da_agent_experience
                        (experience_id, kind, title, when_text, inject_mode, scope, agent_id, content,
                         source_task_ids_json, digest_topic_id, status, updated_by)
                    VALUES
                        ('exp_partial_1', 'memory', '待审部分迁移', '退货时', 'on_demand', 'agent', 'agent_sales',
                         '部分迁移保留内容', '["task_p1"]', 'topic_partial_1', 'pending', '')
                    """
                )
                # Partial DDL state: kind index and kind column dropped, inject_mode and TEXT content remain,
                # digest_target_agent_id not yet added, alembic_version still stamped at 20261127_000046.
                cur.execute("DROP INDEX idx_da_agent_experience_kind ON da_agent_experience")
                cur.execute("ALTER TABLE da_agent_experience DROP COLUMN kind")
        finally:
            conn.close()

        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "20261127_000047"],
            cwd=str(BACKEND_ROOT),
            env=env,
            check=True,
        )

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
                cur.execute(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_SET_NAME, IS_NULLABLE
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'da_agent_experience'
                    """,
                    (schema,),
                )
                cols = {row["COLUMN_NAME"]: row for row in cur.fetchall()}
                assert "kind" not in cols
                assert "inject_mode" not in cols
                assert cols["content"]["DATA_TYPE"].lower() == "mediumtext"
                assert cols["content"]["CHARACTER_SET_NAME"] == "utf8mb4"
                assert cols["content"]["IS_NULLABLE"] == "NO"

                cur.execute(
                    """
                    SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'da_agent_topic' AND COLUMN_NAME = 'digest_target_agent_id'
                    """,
                    (schema,),
                )
                assert cur.fetchone() is not None

                cur.execute("SELECT * FROM da_agent_experience WHERE experience_id = 'exp_partial_1'")
                row = cur.fetchone()
                assert row is not None
                assert row["status"] == "pending"
                assert row["digest_topic_id"] == "topic_partial_1"
                assert row["content"] == "部分迁移保留内容"
        finally:
            conn.close()
