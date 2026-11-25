from __future__ import annotations

import sys
import inspect
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from core.topic_task_store import (
    TopicTaskStore,
    WIDGET_EVENT_TYPES,
    MAX_WIDGET_EVENTS_PER_BATCH,
    _parse_client_ts,
)


def test_get_message_accepts_request_context_for_scoped_callers():
    signature = inspect.signature(TopicTaskStore.get_message)

    assert "context" in signature.parameters


def test_get_message_applies_topic_context_predicate(monkeypatch):
    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.conn.executed.append((sql, list(params)))

        def fetchone(self):
            return self.conn.rows.pop(0)

    class FakeConnection:
        def __init__(self):
            self.executed = []
            self.rows = [
                {
                    "message_id": "msg_1",
                    "topic_id": "topic_1",
                    "task_id": "task_1",
                    "sender_type": "user",
                    "type": "chat",
                    "status": "success",
                    "content": "问题",
                    "event": "",
                    "steps_json": None,
                    "tool_json": None,
                    "seq_id": 1,
                    "correlation_id": None,
                    "parent_correlation_id": None,
                    "content_type": None,
                    "usage_json": None,
                    "feedback": "",
                    "error_json": None,
                    "show_in_ui": 1,
                    "created_at": None,
                    "updated_at": None,
                }
            ]

        def cursor(self):
            return FakeCursor(self)

        def close(self):
            return None

    store = TopicTaskStore()
    conn = FakeConnection()
    monkeypatch.setattr(store, "_ensure_ready", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda database: conn)

    message = store.get_message("msg_1", context={"source": "web", "user_id": 42})

    assert message["message_id"] == "msg_1"
    sql, params = conn.executed[0]
    assert "JOIN da_agent_topic t ON t.topic_id = m.topic_id" in sql
    assert "t.user_id = %s" in sql
    assert params == ["msg_1", "web", "widget", "remote", 42]


def test_normalize_message_row_only_attaches_history_to_assistant():
    store = TopicTaskStore()

    assistant = store._normalize_message_row(  # noqa: SLF001
        {
            "message_id": "msg_1",
            "topic_id": "topic_1",
            "task_id": "task_1",
            "sender_type": "assistant",
            "type": "assistant",
            "status": "finished",
            "content": "最终结果",
            "event": "",
            "steps_json": None,
            "tool_json": None,
            "seq_id": 2,
            "correlation_id": None,
            "parent_correlation_id": None,
            "content_type": None,
            "usage_json": None,
            "feedback": "like",
            "error_json": None,
            "show_in_ui": 1,
            "created_at": None,
            "updated_at": None,
        },
        history={"blocks": [{"block_id": "main_1", "type": "main_text", "text": "最终结果", "status": "success"}], "resume_after_seq": 12},
    )
    user = store._normalize_message_row(  # noqa: SLF001
        {
            "message_id": "msg_2",
            "topic_id": "topic_1",
            "task_id": "task_1",
            "sender_type": "user",
            "type": "chat",
            "status": "success",
            "content": "问题",
            "event": "",
            "steps_json": None,
            "tool_json": None,
            "seq_id": 1,
            "correlation_id": None,
            "parent_correlation_id": None,
            "content_type": None,
            "usage_json": None,
            "error_json": None,
            "show_in_ui": 1,
            "created_at": None,
            "updated_at": None,
        },
        history={"blocks": [{"block_id": "ignored", "type": "thinking", "text": "忽略", "status": "success"}], "resume_after_seq": 9},
    )

    assert assistant["blocks"][0]["type"] == "main_text"
    assert assistant["resume_after_seq"] == 12
    assert assistant["feedback"] == "like"
    assert "blocks" not in user
    assert "resume_after_seq" not in user
    assert user["feedback"] == ""


# ---------------------------------------------------------------------------
# record_widget_events
# ---------------------------------------------------------------------------

def _make_store_with_fake_db(inserted_rows):
    """Return a TopicTaskStore whose _connect returns a fake connection."""

    class FakeCursor:
        def __init__(self):
            self.executed_many = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def executemany(self, sql, rows):
            for row in rows:
                inserted_rows.append(row)

    class FakeConn:
        def __init__(self):
            self._cursor = FakeCursor()

        def cursor(self):
            return self._cursor

        def commit(self):
            pass

        def close(self):
            pass

    store = TopicTaskStore()
    store._ready = True

    fake_conn = FakeConn()
    store._connect = lambda database=None: fake_conn
    store._schema_name = lambda: "dataagent"
    return store


def test_record_widget_events_persists_valid_events():
    rows = []
    store = _make_store_with_fake_db(rows)
    context = {"source": "widget", "website_id": "site1", "user_id": 7, "shared_session_key": ""}

    accepted = store.record_widget_events(
        [
            {"event_type": "widget_open", "agent_id": "agent_a"},
            {"event_type": "message_send", "payload": {"input_source": "typed", "length": 10}},
        ],
        context,
    )

    assert accepted == 2
    assert len(rows) == 2
    # Row tuple: (event_type, source, website_id, user_id, external_user_id, visitor_id, agent_id, topic_id, task_id, message_id, payload_json, client_ts)
    assert rows[0][0] == "widget_open"
    assert rows[0][1] == "widget"
    assert rows[0][2] == "site1"
    assert rows[0][3] == 7
    assert rows[1][0] == "message_send"
    assert rows[1][10] is not None  # payload_json


def test_record_widget_events_rejects_unknown_event_types():
    rows = []
    store = _make_store_with_fake_db(rows)
    context = {"source": "widget", "website_id": "site1", "user_id": 7}

    accepted = store.record_widget_events(
        [
            {"event_type": "unknown_event"},
            {"event_type": "widget_open"},
        ],
        context,
    )

    assert accepted == 1
    assert rows[0][0] == "widget_open"


def test_record_widget_events_ignores_oversized_payload():
    rows = []
    store = _make_store_with_fake_db(rows)
    context = {"source": "widget", "website_id": "site1", "user_id": 7}
    big_payload = {"data": "x" * 5000}

    accepted = store.record_widget_events(
        [{"event_type": "widget_open", "payload": big_payload}],
        context,
    )

    assert accepted == 1
    assert rows[0][10] is None  # payload_json dropped


def test_record_widget_events_enforces_batch_limit():
    rows = []
    store = _make_store_with_fake_db(rows)
    context = {"source": "widget", "website_id": "site1", "user_id": 7}
    events = [{"event_type": "widget_open"}] * (MAX_WIDGET_EVENTS_PER_BATCH + 10)

    accepted = store.record_widget_events(events, context)

    assert accepted == MAX_WIDGET_EVENTS_PER_BATCH


def test_record_widget_events_uses_only_header_identity():
    rows = []
    store = _make_store_with_fake_db(rows)
    # Identity from context only — body fields should be ignored
    context = {"source": "widget", "website_id": "real_site", "user_id": 9}

    store.record_widget_events(
        [{"event_type": "widget_open", "website_id": "injected_site", "user_id": 999, "external_user_id": "injected_user"}],
        context,
    )

    assert rows[0][2] == "real_site"
    assert rows[0][3] == 9
    assert rows[0][4] == ""


def test_record_widget_events_returns_zero_for_empty_list():
    rows = []
    store = _make_store_with_fake_db(rows)
    context = {"source": "widget", "website_id": "site1", "external_user_id": "u1", "visitor_id": ""}

    accepted = store.record_widget_events([], context)

    assert accepted == 0
    assert rows == []


def test_parse_client_ts_handles_iso_and_z_suffix():
    from datetime import datetime

    ts = _parse_client_ts("2026-05-29T10:00:00Z")
    assert isinstance(ts, datetime)

    ts2 = _parse_client_ts("2026-05-29T10:00:00")
    assert isinstance(ts2, datetime)

    assert _parse_client_ts("") is None
    assert _parse_client_ts("not-a-date") is None


def test_widget_event_types_allowlist_contains_expected_values():
    assert "widget_open" in WIDGET_EVENT_TYPES
    assert "widget_close" in WIDGET_EVENT_TYPES
    assert "history_open" in WIDGET_EVENT_TYPES
    assert "history_close" in WIDGET_EVENT_TYPES
    assert "conversation_new" in WIDGET_EVENT_TYPES
    assert "message_send" in WIDGET_EVENT_TYPES


def test_admin_list_topics_scopes_to_widget_and_forwards_filters(monkeypatch):
    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.conn.executed.append((sql, list(params)))

        def fetchone(self):
            return {"total": 1}

        def fetchall(self):
            return [
                {
                    "topic_id": "topic_widget_1",
                    "title": "嵌入站会话",
                    "chat_topic_id": "chat_1",
                    "chat_conversation_id": "conv_1",
                    "current_task_id": None,
                    "current_task_status": None,
                    "source": "widget",
                    "website_id": "site_a",
                    "external_user_id": "",
                    "visitor_id": "visitor_x",
                    "agent_id": "agent_default",
                    "agent_snapshot_json": None,
                    "created_at": None,
                    "updated_at": None,
                    "message_count": 3,
                    "last_message_preview": "最近一条",
                }
            ]

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self):
            return FakeCursor(self)

        def close(self):
            return None

    store = TopicTaskStore()
    conn = FakeConnection()
    monkeypatch.setattr(store, "_ensure_ready", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda database: conn)

    result = store.admin_list_topics(website_id="site_a", visitor_id="visitor_x", page=2, page_size=10)

    assert result["total"] == 1
    assert result["page"] == 2
    assert result["items"][0]["source"] == "widget"
    assert result["items"][0]["website_id"] == "site_a"

    count_sql, count_params = conn.executed[0]
    assert "COUNT(*)" in count_sql
    assert "t.source = %s" in count_sql
    assert count_params == ["widget", "site_a", "visitor_x"]

    list_sql, list_params = conn.executed[1]
    assert "LIMIT %s OFFSET %s" in list_sql
    # widget source + filters first, then pagination (page 2, size 10 -> offset 10)
    assert list_params == ["widget", "site_a", "visitor_x", 10, 10]
    # Per-user isolation predicate must never be reused by the admin path.
    assert "COALESCE(t.source" not in list_sql


def test_admin_list_widget_users_groups_users_and_forwards_filters(monkeypatch):
    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.conn.executed.append((sql, list(params)))

        def fetchall(self):
            return [
                {"kind": "ext", "user_id": "u_10086", "topic_count": 3, "last_active_at": None},
                {"kind": "vis", "user_id": "", "topic_count": 1, "last_active_at": None},
            ]

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self):
            return FakeCursor(self)

        def close(self):
            return None

    store = TopicTaskStore()
    conn = FakeConnection()
    monkeypatch.setattr(store, "_ensure_ready", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda database: conn)

    users = store.admin_list_widget_users(website_id="site_a", keyword="u_", limit=50)

    # Rows without a user_id are dropped; counts are coerced to int.
    assert users == [{"kind": "ext", "user_id": "u_10086", "topic_count": 3}]

    sql, params = conn.executed[0]
    assert "GROUP BY kind, user_id" in sql
    assert "t.source = %s" in sql
    # widget source + website + keyword (LIKE twice) + limit
    assert params == ["widget", "site_a", "%u_%", "%u_%", 50]
    # Admin facet must not reuse the per-user isolation predicate.
    assert "COALESCE(t.source" not in sql


# ---------------------------------------------------------------------------
# 统一用户可见性谓词矩阵
# ---------------------------------------------------------------------------

def test_predicate_fail_closed_without_user_id():
    store = TopicTaskStore()
    sql, params = store._topic_context_predicate({"source": "web"}, alias="t")  # noqa: SLF001
    assert sql == "1 = 0"
    assert params == []

    sql, params = store._topic_context_predicate(  # noqa: SLF001
        {"source": "widget", "website_id": "demo"}, alias="t"
    )
    assert sql == "1 = 0"
    assert params == []


def test_predicate_unified_account_matrix():
    store = TopicTaskStore()

    # 普通用户 web：看自己跨 web / 个人 widget / remote 话题，不含共享池。
    sql, params = store._topic_context_predicate(  # noqa: SLF001
        {"source": "web", "user_id": 42, "is_admin": False}, alias="t"
    )
    assert sql == f"t.source IN (%s, %s, %s) AND t.user_id = %s AND t.is_agent_preview = 0"
    assert params == ["web", "widget", "remote", 42]

    # 管理员 web：看自己全部跨入口话题 + 自己的 web 调试话题。
    sql, params = store._topic_context_predicate(  # noqa: SLF001
        {"source": "web", "user_id": 1, "is_admin": True}, alias="t"
    )
    assert sql == f"t.source IN (%s, %s, %s) AND t.user_id = %s AND (t.is_agent_preview = 0 OR t.source = %s)"
    assert params == ["web", "widget", "remote", 1, "web"]

    # 个人模式 widget：看自己 web + remote + 该站点的个人 widget 话题。
    sql, params = store._topic_context_predicate(  # noqa: SLF001
        {"source": "widget", "website_id": "demo", "user_id": 42}, alias="t"
    )
    assert sql == (
        "(t.source IN (%s, %s) OR (t.source = %s AND t.website_id = %s)) "
        "AND t.user_id = %s AND t.is_agent_preview = 0"
    )
    assert params == ["web", "remote", "widget", "demo", 42]

    # context=None（管理端点）不过滤。
    sql, params = store._topic_context_predicate(None, alias="t")  # noqa: SLF001
    assert sql == "1 = 1"
    assert params == []


def test_create_topic_writes_user_id(monkeypatch):
    class FakeCursor:
        def __init__(self, conn):
            self.conn = conn

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params):
            self.conn.executed.append((sql, list(params)))

    class FakeConnection:
        def __init__(self):
            self.executed = []

        def cursor(self):
            return FakeCursor(self)

        def commit(self):
            return None

        def close(self):
            return None

    store = TopicTaskStore()
    conn = FakeConnection()
    monkeypatch.setattr(store, "_ensure_ready", lambda: None)
    monkeypatch.setattr(store, "_connect", lambda database: conn)
    monkeypatch.setattr(store, "get_topic", lambda topic_id, context=None: {"topic_id": topic_id})

    store.create_topic(
        title="趋势分析",
        context={"source": "web", "user_id": 42, "is_admin": False},
    )

    sql, params = conn.executed[0]
    assert "user_id, external_user_id" in sql
    assert params[-3:] == [42, "", 0]


# ---------------------------------------------------------------------------
# system prompt persistence
# ---------------------------------------------------------------------------

def _make_store_with_sql_log(executed, fetched=None):
    """``fetched`` is the row (or list of rows, consumed in order) fetchone returns."""
    pending = list(fetched) if isinstance(fetched, list) else [fetched]

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), tuple(params)))

        def fetchone(self):
            return pending.pop(0) if pending else None

    class FakeConn:
        def cursor(self):
            return FakeCursor()

        def commit(self):
            executed.append(("COMMIT", ()))

        def rollback(self):
            executed.append(("ROLLBACK", ()))

        def close(self):
            pass

    store = TopicTaskStore()
    store._ready = True
    store._connect = lambda database=None, **kwargs: FakeConn()
    store._schema_name = lambda: "dataagent"
    return store


def test_record_task_system_prompt_dedupes_by_content_hash_and_links_task():
    import hashlib

    executed = []
    store = _make_store_with_sql_log(executed, fetched={"1": 1})
    prompt = "系统提示 v1"

    digest = store.record_task_system_prompt("task_1", prompt)

    expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert digest == expected
    lock_sql, lock_params = executed[0]
    assert lock_sql == "SELECT 1 FROM da_agent_task WHERE task_id = %s FOR UPDATE"
    assert lock_params == ("task_1",)
    insert_sql, insert_params = executed[1]
    assert insert_sql.startswith("INSERT IGNORE INTO da_system_prompt")
    assert insert_params == (expected, prompt, len(prompt.encode("utf-8")))
    update_sql, update_params = executed[2]
    assert "UPDATE da_agent_task SET system_prompt_sha256 = %s WHERE task_id = %s" in update_sql
    assert update_params == (expected, "task_1")
    assert executed[3] == ("COMMIT", ())


def test_record_task_system_prompt_writes_nothing_for_missing_task():
    executed = []
    store = _make_store_with_sql_log(executed, fetched=None)

    assert store.record_task_system_prompt("task_missing", "系统提示") is None
    assert [sql for sql, _ in executed] == [
        "SELECT 1 FROM da_agent_task WHERE task_id = %s FOR UPDATE",
        "ROLLBACK",
    ]


def test_record_task_system_prompt_rejects_oversized_content(monkeypatch):
    import core.topic_task_store as topic_task_store

    executed = []
    store = _make_store_with_sql_log(executed, fetched={"1": 1})
    monkeypatch.setattr(topic_task_store, "SYSTEM_PROMPT_MAX_BYTES", 8)

    try:
        store.record_task_system_prompt("task_1", "超过八个字节")
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("oversized prompt must be rejected before any write")
    assert executed == []


def test_get_task_system_prompt_returns_none_when_not_recorded():
    store = _make_store_with_sql_log([], fetched=None)

    assert store.get_task_system_prompt("task_missing") is None


def test_get_task_system_prompt_serializes_record():
    from datetime import datetime

    row = {
        "task_id": "task_1",
        "prompt_sha256": "a" * 64,
        "byte_length": 12,
        "created_at": datetime(2026, 10, 22, 12, 0, 0),
        "content": "系统提示",
    }
    store = _make_store_with_sql_log([], fetched=row)

    record = store.get_task_system_prompt("task_1")

    assert record == {
        "task_id": "task_1",
        "prompt_sha256": "a" * 64,
        "byte_length": 12,
        "created_at": "2026-10-22T12:00:00",
        "content": "系统提示",
    }
