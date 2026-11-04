"""Real MySQL transaction contracts; opt in with DATAAGENT_TEST_MYSQL=1."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).parents[1]))
from core.topic_task_store import TopicTaskStore

pytestmark = pytest.mark.skipif(os.environ.get("DATAAGENT_TEST_MYSQL") != "1", reason="local MySQL integration opt-in")


@pytest.fixture
def task():
    store = TopicTaskStore()
    topic = store.create_topic(title="checkpoint-test")
    task = store.create_task(topic_id=topic["topic_id"],prompt="test",provider_id="local-test",model="stub",
        database_hint=None,debug=False,timeout_seconds=1800,sql_read_timeout_seconds=300,sql_write_timeout_seconds=60)
    store.mark_task_running(task["task_id"])
    try: yield store,topic["topic_id"],task["task_id"]
    finally: store.delete_topic(topic["topic_id"])


def commit_card(store,topic_id,task_id,kind="question",version=0):
    request={"request_id":"request-1","kind":kind,"payload":{"questions":[],"tool_name":"write-record","arguments":{"x":1}}}
    record="question_request" if kind=="question" else "permission_request"
    store.commit_session_segment(task_id=task_id,expected_version=version,
        checkpoint={"header":{"type":"session","version":3},"entries":[],"leaf_id":None},
        state={"interaction":request,"usage":{"turns":1}},
        card={"record_type":record,"data":{"request_id":"request-1","questions":[],"tool_name":"write-record"}})


def test_card_and_checkpoint_are_atomic_and_a_stale_writer_is_rejected(task):
    store,topic_id,task_id=task
    with pytest.raises(KeyError):
        store.commit_session_segment(task_id=task_id,expected_version=0,
            checkpoint={"header":{},"entries":[],"leaf_id":None},state={},card={"bad":True})
    assert store.get_session_checkpoint(topic_id) is None
    assert store.get_pending_question_request(task_id) is None
    assert store.get_task(task_id)["task_status"]=="running"
    commit_card(*task)
    assert store.get_task(task_id)["task_status"]=="waiting_input"
    restored=TopicTaskStore().get_session_checkpoint(topic_id)
    assert restored["state"]["usage"]["turns"]==1
    store.set_task_status(task_id,"running")
    with pytest.raises(ValueError,match="Stale"):
        commit_card(*task,version=0)
    assert store.get_session_checkpoint(topic_id)["version"]==1


def test_concurrent_answers_are_recorded_once_and_requeue_the_same_task(task):
    store,topic_id,task_id=task
    commit_card(*task)
    def answer(_):
        return store.append_question_answer_if_waiting(task_id=task_id,request_id="request-1",answers=[{"selected":["A"]}])
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(answer,range(2)))
    assert sorted(outcomes)==["already_resolved","appended"]
    assert store.get_task(task_id)["task_status"]=="waiting"
    assert store.get_session_checkpoint(topic_id)["state"]["interaction"]["result"]["answers"]==[{"selected":["A"]}]
    assert store.append_question_answer_if_waiting(task_id=task_id,request_id="stale-request",answers=[])=="not_found"


def test_confirmed_operation_is_claimed_once_and_unknown_results_are_not_replayed(task):
    store,topic_id,task_id=task
    commit_card(*task,kind="permission")
    assert store.append_permission_decision_if_waiting(task_id=task_id,request_id="request-1",decision="allow")=="appended"
    claimed=store.claim_confirmed_operation(topic_id=topic_id,task_id=task_id,request_id="request-1")
    assert claimed["operation_status"]=="executing"
    with pytest.raises(ValueError,match="OPERATION_RESULT_UNKNOWN"):
        store.claim_confirmed_operation(topic_id=topic_id,task_id=task_id,request_id="request-1")
    store.save_confirmed_operation_result(topic_id=topic_id,task_id=task_id,request_id="request-1",result={"ok":True})
    cached=store.claim_confirmed_operation(topic_id=topic_id,task_id=task_id,request_id="request-1")
    assert cached["operation_status"]=="done"
    assert cached["operation_result"]=={"ok":True}
