"""In-process live channel for a running task segment.

Design: docs/design/2026-11-13-content-delta-live-stream-design.md.

``content.delta`` never reaches MySQL. While a segment runs, the event writer
publishes each delta here, and a marker (``seq_id`` only) for every record it
persists; the SSE route reads persistent rows from MySQL up to that marker, so
row order and shape stay exactly what the database returns. A subscriber also
gets a snapshot of every block still being generated, so a page opened or
reconnected mid-block shows the text produced so far.

Everything runs on the backend event loop: the writer calls ``publish``
synchronously from the run loop, so ``subscribe`` (queue plus snapshot) cannot
interleave with a publish. The SSE must therefore be served by the process
running the task (single-process deployment; see docs/TODO.md).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

QUEUE_LIMIT = 1000


@dataclass(eq=False)
class Subscription:
    task_id: str
    queue: asyncio.Queue
    snapshot: list[dict[str, Any]]
    closed: bool = False


@dataclass
class _Channel:
    snapshot: Callable[[], list[dict[str, Any]]]
    subscribers: set[Subscription] = field(default_factory=set)


_channels: dict[str, _Channel] = {}


def open_channel(task_id: str, snapshot: Callable[[], list[dict[str, Any]]]) -> None:
    """Start the live channel for one execution segment of ``task_id``."""
    close_channel(task_id)
    _channels[task_id] = _Channel(snapshot=snapshot)


def close_channel(task_id: str) -> None:
    """End the segment's channel; subscribers fall back to database polling."""
    channel = _channels.pop(task_id, None)
    if channel is None:
        return
    for sub in channel.subscribers:
        _end(sub)
    channel.subscribers.clear()


def is_live(task_id: str) -> bool:
    return task_id in _channels


def publish(task_id: str, record: dict[str, Any]) -> None:
    """Deliver one record to every subscriber. A full queue drops that subscriber."""
    channel = _channels.get(task_id)
    if channel is None:
        return
    for sub in list(channel.subscribers):
        try:
            sub.queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("live_events.subscriber_dropped task_id=%s reason=queue_full", task_id)
            channel.subscribers.discard(sub)
            sub.closed = True


def subscribe(task_id: str) -> Subscription | None:
    """Queue plus a snapshot of the blocks still being generated, or None when not live."""
    channel = _channels.get(task_id)
    if channel is None:
        return None
    sub = Subscription(task_id=task_id, queue=asyncio.Queue(maxsize=QUEUE_LIMIT), snapshot=channel.snapshot())
    channel.subscribers.add(sub)
    return sub


def unsubscribe(sub: Subscription) -> None:
    channel = _channels.get(sub.task_id)
    if channel is not None:
        channel.subscribers.discard(sub)
    sub.closed = True


def _end(sub: Subscription) -> None:
    sub.closed = True
    try:
        sub.queue.put_nowait(None)
    except asyncio.QueueFull:
        pass
