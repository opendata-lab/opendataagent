"""Real MySQL coverage for unified account ownership and shared Widget sessions."""

import os
from uuid import uuid4

import pytest

from core.accounts import get_account_store, hash_password
from core.topic_task_store import TopicTaskStore


@pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")
def test_unified_account_and_shared_widget_isolation():
    acc_store = get_account_store()
    store = TopicTaskStore()
    suffix = uuid4().hex[:8]
    u1 = acc_store.create_user(username=f"u1_{suffix}", password_hash=hash_password("pw1"), display_name="U1")
    u2 = acc_store.create_user(username=f"u2_{suffix}", password_hash=hash_password("pw2"), display_name="U2")

    web = {"source": "web", "user_id": u1.id, "is_admin": False}
    admin = {"source": "web", "user_id": u1.id, "is_admin": True}
    remote = {"source": "remote", "user_id": u1.id, "is_admin": False}
    widget = {"source": "widget", "website_id": "test-shared", "user_id": u1.id}
    other_site = {"source": "widget", "website_id": "test-other", "user_id": u1.id}
    other_user = {"source": "widget", "website_id": "test-shared", "user_id": u2.id}
    shared_a = {"source": "widget", "website_id": "onto-test", "user_id": u1.id, "shared_session_key": f"s_{suffix}"}
    shared_b = {"source": "widget", "website_id": "onto-test", "user_id": u2.id, "shared_session_key": f"s_{suffix}"}
    shared_other = {"source": "widget", "website_id": "onto-test", "user_id": u1.id, "shared_session_key": f"other_{suffix}"}
    topics = []

    def create(context):
        topic = store.create_topic(title="shared-ownership-test", context=context)
        topics.append(topic["topic_id"])
        return topic["topic_id"]

    def visible(context):
        return {row["topic_id"] for row in store.list_topics(context=context)} & set(topics)

    try:
        web_id = create(web)
        remote_id = create(remote)
        widget_id = create(widget)
        other_site_id = create(other_site)
        other_user_id = create(other_user)
        shared_id = create(shared_a)

        assert visible(web) == {web_id, remote_id, widget_id, other_site_id}
        assert visible(admin) == {web_id, remote_id, widget_id, other_site_id}
        assert store.get_topic(other_user_id, context=admin) is None
        assert visible(widget) == {web_id, remote_id, widget_id}
        assert visible(other_user) == {other_user_id}
        assert visible(shared_a) == {shared_id}
        assert visible(shared_b) == {shared_id}
        assert visible(shared_other) == set()
        assert store.get_topic(shared_id, context=web) is None
    finally:
        for topic_id in topics:
            store.delete_topic(topic_id)
