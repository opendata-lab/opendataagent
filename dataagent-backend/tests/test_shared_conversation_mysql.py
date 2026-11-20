"""Real MySQL coverage for shared account and legacy Widget ownership."""

import os
from uuid import uuid4

import pytest

from core.topic_task_store import TopicTaskStore


@pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")
def test_signed_widget_and_platform_share_only_the_same_account(monkeypatch):
    monkeypatch.setattr("core.topic_task_store.is_auth_enabled", lambda: True)
    store = TopicTaskStore()
    uid = f"test:{uuid4().hex}"
    web = {"source": "web", "auth_user_id": uid}
    admin = {**web, "auth_role": "admin"}
    widget = {"source": "widget", "website_id": "test-shared", "auth_user_id": uid}
    legacy = {"source": "widget", "website_id": "test-shared", "external_user_id": uid}
    case_variant = uid.upper()
    other_user = {**widget, "auth_user_id": case_variant}
    other_site = {**widget, "website_id": "test-other"}
    topics = []

    def create(context):
        topic = store.create_topic(title="shared-ownership-test", context=context)
        topics.append(topic["topic_id"])
        return topic["topic_id"]

    def visible(context):
        return {row["topic_id"] for row in store.list_topics(context=context)} & set(topics)

    try:
        web_id = create(web)
        widget_id = create(widget)
        legacy_id = create(legacy)
        other_user_id = create(other_user)
        other_site_id = create(other_site)

        assert visible(web) == {web_id, widget_id, other_site_id}
        assert visible(admin) == {web_id, widget_id, other_site_id}
        assert store.get_topic(other_user_id, context=admin) is None
        assert visible(widget) == {web_id, widget_id}
        assert visible(legacy) == {legacy_id}
        assert visible(other_user) == {other_user_id}
        assert store.get_topic(widget_id, context=web)["source"] == "widget"
        assert store.get_topic(web_id, context=widget)["source"] == "web"
        assert store.get_topic(widget_id, context=other_user) is None
        assert store.get_topic(widget_id, context=other_site) is None
    finally:
        for topic_id in topics:
            store.delete_topic(topic_id)
