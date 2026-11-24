"""Widget conversation isolation (trusted shared sessions & personal accounts)."""
from __future__ import annotations

import inspect
import pytest

from core import topic_task_store


def shared_widget_context(session_key, website_id="ontofoundry", user_id=None):
    return {
        "source": "widget",
        "website_id": website_id,
        "user_id": user_id,
        "is_admin": False,
        "shared_session_key": session_key,
    }


def personal_widget_context(user_id, website_id="opendataworks-portal"):
    return {
        "source": "widget",
        "website_id": website_id,
        "user_id": user_id,
        "is_admin": False,
        "shared_session_key": "",
    }


def bare_store():
    return topic_task_store.TopicTaskStore.__new__(topic_task_store.TopicTaskStore)


class TestContextPredicate:
    def test_shared_session_is_scoped_to_site_and_session_key(self):
        sql, params = bare_store()._topic_context_predicate(
            shared_widget_context("ontofoundry:w-1:s-1")
        )
        assert "source" in sql and "website_id" in sql and "external_user_id" in sql
        assert params == ["widget", "ontofoundry", "ontofoundry:w-1:s-1"]

    def test_two_shared_sessions_on_one_site_produce_different_predicates(self):
        _, a = bare_store()._topic_context_predicate(shared_widget_context("ontofoundry:w-1:s-1"))
        _, b = bare_store()._topic_context_predicate(shared_widget_context("ontofoundry:w-1:s-2"))
        assert a != b

    def test_personal_widget_is_scoped_by_integer_user_id_and_excludes_shared(self):
        sql, params = bare_store()._topic_context_predicate(personal_widget_context(42))
        assert "user_id = %s" in sql
        assert "external_user_id = ''" in sql
        assert 42 in params
        assert "opendataworks-portal" in params

    def test_unauthenticated_context_fails_closed(self):
        sql, params = bare_store()._topic_context_predicate({"source": "widget", "website_id": "demo"})
        assert sql == "1 = 0"
        assert params == []


class TestTaskReadsAreScoped:
    def test_get_task_threads_the_request_context_into_its_query(self, monkeypatch):
        seen = {}

        def fake_predicate(self, context=None, alias=""):
            seen["context"] = context
            seen["alias"] = alias
            return "1=1", []

        monkeypatch.setattr(topic_task_store.TopicTaskStore, "_topic_context_predicate", fake_predicate)
        monkeypatch.setattr(topic_task_store.TopicTaskStore, "_ensure_ready", lambda self: None)

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, *_args, **_kwargs):
                return None

            def fetchone(self):
                return None

        class Conn:
            def cursor(self):
                return Cursor()

            def close(self):
                return None

        monkeypatch.setattr(topic_task_store.TopicTaskStore, "_connect", lambda self, **_: Conn())
        monkeypatch.setattr(topic_task_store.TopicTaskStore, "_schema_name", lambda self: "public")

        store = topic_task_store.TopicTaskStore.__new__(topic_task_store.TopicTaskStore)
        context = shared_widget_context("ontofoundry:w-1:s-1")
        store.get_task("t-1", context=context)

        assert seen["context"] == context
        assert seen["alias"] == "topic"

    @pytest.mark.parametrize(
        "route_name",
        [
            "api_get_task",
            "api_get_task_message",
            "api_stream_sdk_events",
            "api_cancel_task",
            "_record_permission_decision",
            "_record_question_answer",
        ],
    )
    def test_every_task_route_resolves_account_context(self, route_name):
        from api import routes

        source = inspect.getsource(getattr(routes, route_name))
        assert "current_account_context(" in source, (
            f"{route_name} must resolve current_account_context before reading a task"
        )
