from __future__ import annotations

import asyncio

import anyio

from core.task_coordinator import TaskCoordinator


class _FakeRedis:
    def __init__(self, lease_owner: str):
        self._lease_owner = lease_owner
        self.expire_calls = 0

    async def get(self, key: str):
        return self._lease_owner

    async def expire(self, key: str, ttl: int):
        self.expire_calls += 1
        return True


def test_heartbeat_tick_renews_lease_while_queued_without_db_heartbeat():
    class FakeStore:
        def __init__(self):
            self.heartbeats = 0

        def heartbeat_task(self, task_id):
            self.heartbeats += 1

    coordinator = TaskCoordinator(store=FakeStore())
    coordinator._redis = _FakeRedis(coordinator.instance_id)
    running = asyncio.Event()

    async def run():
        queued = await coordinator._heartbeat_tick("task-1", running=running)
        running.set()
        active = await coordinator._heartbeat_tick("task-1", running=running)
        return queued, active

    queued, active = anyio.run(run)

    # Lease is renewed in both phases ...
    assert queued is True
    assert active is True
    assert coordinator._redis.expire_calls == 2
    # ... but the DB heartbeat is only written once the task is actually running.
    assert coordinator.store.heartbeats == 1


def test_heartbeat_tick_reports_lost_lease():
    class FakeStore:
        def heartbeat_task(self, task_id):
            pass

    coordinator = TaskCoordinator(store=FakeStore())
    # Lease is held by a different instance -> renewal must fail.
    coordinator._redis = _FakeRedis("another-instance")
    running = asyncio.Event()
    running.set()

    assert anyio.run(lambda: coordinator._heartbeat_tick("task-1", running=running)) is False


def test_task_stop_reason_distinguishes_lease_loss_from_user_cancel():
    class FakeStore:
        def __init__(self):
            self.cancel_requested = False

        def is_task_cancel_requested(self, task_id):
            return self.cancel_requested

    store = FakeStore()
    coordinator = TaskCoordinator(store=store)

    async def run():
        lease_lost = asyncio.Event()
        none_reason = await coordinator._task_stop_reason("task-1", lease_lost)
        store.cancel_requested = True
        user_reason = await coordinator._task_stop_reason("task-1", lease_lost)
        lease_lost.set()
        runner_reason = await coordinator._task_stop_reason("task-1", lease_lost)
        return none_reason, user_reason, runner_reason

    assert anyio.run(run) == (None, "user_cancel", "runner_stop")


def test_recover_checkpointed_cards_without_a_live_process():
    class Store:
        def __init__(self): self.state={"interaction":{"request_id":"r1"}}; self.finished=[]; self.status=[]
        def list_parked_tasks(self,**kw): return [{"task_id":"task-1","topic_id":"topic-1"}]
        def get_session_checkpoint(self,_): return {"task_id":"task-1","state":self.state}
        def is_task_cancel_requested(self,_): return False
        def set_task_status(self,*args): self.status.append(args)
        def update_assistant_message(self,**kw): pass
        def finish_task(self,**kw): self.finished.append(kw)
    store=Store(); coordinator=TaskCoordinator(store=store)
    queued=[]
    async def submit(task_id): queued.append(task_id)
    coordinator.submit_task=submit
    anyio.run(lambda:coordinator._recover_parked_tasks(batch_size=10))
    assert not store.finished and not queued
    store.state["interaction"]["result"]={"answers":[]}
    anyio.run(lambda:coordinator._recover_parked_tasks(batch_size=10))
    assert queued==["task-1"] and store.status==[("task-1","waiting")]


def test_execution_identity_prefers_dataagent_login_then_host_reported_widget_user():
    from core.task_coordinator import _execution_user_id

    assert _execution_user_id({"auth_user_id": "local:alice", "external_user_id": "local:bob"}) == "local:alice"
    assert _execution_user_id({"auth_user_id": "", "external_user_id": "local:bob"}) == "local:bob"
    assert _execution_user_id({"auth_user_id": "", "external_user_id": "", "visitor_id": "v1"}) == ""


def test_fast_card_answer_is_queued_after_previous_segment_releases_lease():
    class Store:
        def get_task(self, task_id): return {'task_status': 'waiting'}
    coordinator = TaskCoordinator(store=Store())
    queued = []
    async def submit(task_id):
        assert task_id not in coordinator._active_tasks
        queued.append(task_id)
    coordinator.submit_task = submit
    async def run():
        coordinator._active_tasks['task-1'] = object()
        coordinator._task_done('task-1')
        await asyncio.sleep(0)
    anyio.run(run)
    assert queued == ['task-1']
