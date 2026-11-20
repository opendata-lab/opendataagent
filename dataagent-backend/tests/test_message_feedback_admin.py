"""Admin 用户反馈 listing against real MySQL; opt in with DATAAGENT_TEST_MYSQL=1."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.topic_task_store import TopicTaskStore  # noqa: E402

pytestmark = pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")


def _turn(store: TopicTaskStore, *, agent_id: str, question: str) -> tuple[str, str]:
    topic = store.create_topic(title=question, agent_snapshot={"agent_id": agent_id, "name": agent_id})
    task = store.create_task(topic_id=topic["topic_id"], prompt=question, provider_id="local-test", model="stub",
        database_hint=None, debug=False, timeout_seconds=1800, sql_read_timeout_seconds=300, sql_write_timeout_seconds=60)
    store.append_user_message(topic_id=topic["topic_id"], task_id=task["task_id"], content=question)
    answer = store.ensure_assistant_message(topic_id=topic["topic_id"], task_id=task["task_id"], status="finished")
    return topic["topic_id"], answer["message_id"]


@pytest.fixture
def turns():
    store = TopicTaskStore()
    made = {
        "liked": _turn(store, agent_id="agent_fb_a", question="上月销售额是多少"),
        "disliked": _turn(store, agent_id="agent_fb_b", question="各渠道退款率"),
        "unrated": _turn(store, agent_id="agent_fb_a", question="没有评价的问题"),
    }
    try:
        yield store, made
    finally:
        for topic_id, _ in made.values():
            store.delete_topic(topic_id)


def _ids(page: dict) -> list[str]:
    return [item["message_id"] for item in page["items"]]


def test_lists_rated_answers_with_question_counts_and_filters(turns):
    store, made = turns
    for key, value in (("liked", "like"), ("disliked", "dislike")):
        topic_id, message_id = made[key]
        assert store.update_message_feedback(topic_id=topic_id, message_id=message_id, feedback=value)

    scope = {"start": "2000-01-01 00:00:00"}
    agents = ["agent_fb_a", "agent_fb_b"]
    page = {**store.list_message_feedback(**scope)}
    mine = [item for item in page["items"] if item["agent_id"] in agents]
    assert sorted(item["feedback"] for item in mine) == ["dislike", "like"]
    disliked = next(item for item in mine if item["feedback"] == "dislike")
    assert disliked["question"] == "各渠道退款率"
    assert disliked["agent_id"] == "agent_fb_b"
    assert disliked["source"] == "web"
    assert disliked["feedback_at"]
    assert made["unrated"][1] not in _ids(page)

    only_b = store.list_message_feedback(agent_id="agent_fb_b", **scope)
    assert _ids(only_b) == [made["disliked"][1]]
    assert (only_b["likes"], only_b["dislikes"]) == (0, 1)

    a_dislikes = store.list_message_feedback(agent_id="agent_fb_a", feedback="dislike", **scope)
    assert a_dislikes["items"] == [] and a_dislikes["total"] == 0
    assert (a_dislikes["likes"], a_dislikes["dislikes"]) == (1, 0)  # counts ignore the rating filter

    # Removing a rating removes the answer from the list.
    topic_id, message_id = made["liked"]
    store.update_message_feedback(topic_id=topic_id, message_id=message_id, feedback="")
    assert store.list_message_feedback(agent_id="agent_fb_a", **scope)["total"] == 0
