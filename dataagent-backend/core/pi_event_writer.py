"""Persist neutral Pi events and frontend interaction records in MySQL."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from core import live_events


logger = logging.getLogger(__name__)


def _parse_occurred_at(value: Any) -> Any:
    """Producer timestamp as a datetime, or None if it is not usable.

    A malformed timestamp must not cost us the record: ordering falls back to
    the row's own created_at, which is close enough for display.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

# Payload keys are deliberately aligned with the SDK path's projected block
# fields (``output`` / ``is_error``) rather than inventing parallel names
# (``result`` / ``error``). Both projections and the frontend render adapter
# ``blockToToolProp`` already read these, so alignment here is what lets a Pi
# turn render through the exact same components as an SDK turn.
TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.failed", "run.cancelled", "run.suspended"}
)


class PiEventWriter:
    """Writes neutral Pi AgentEvents into ``da_agent_sdk_record``."""

    def __init__(self, store: Any, task_id: str, topic_id: str, *, initial_turn_index: int = 0) -> None:
        self._store = store
        self._task_id = task_id
        self._topic_id = topic_id
        self._turn_index = initial_turn_index
        self._highest_sequence = 0
        # Blocks still being generated, keyed by (turn_id, content_id). Their
        # deltas are streamed live and never stored; see flush_open_blocks.
        self._open_blocks: dict[tuple[str, str], dict[str, Any]] = {}

    @property
    def turn_index(self) -> int:
        return self._turn_index

    def ingest(self, event: dict[str, Any]) -> None:
        """Persist one neutral AgentEvent.

        Never raises: a malformed event from the child must not abort the run.
        Out-of-order and replayed events are dropped by sequence, mirroring the
        monotonic guarantee the Cell's run state machine provides.
        """
        try:
            self._ingest_safe(event)
        except Exception:
            logger.exception(
                "pi_event.ingest_failed task_id=%s event_type=%s",
                self._task_id,
                (event or {}).get("type"),
            )

    def _ingest_safe(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            return

        sequence = event.get("sequence")
        if isinstance(sequence, int):
            if sequence <= self._highest_sequence:
                logger.warning(
                    "pi_event.out_of_order task_id=%s sequence=%s highest=%s",
                    self._task_id,
                    sequence,
                    self._highest_sequence,
                )
                return
            self._highest_sequence = sequence

        # A new turn starts a fresh block group, matching the frontend's
        # message_start semantics so history projection groups the same way.
        if event_type == "turn.started":
            self._turn_index += 1

        # A malformed payload must not cost us the event itself: the type and
        # sequence still carry information the projection and the terminal
        # handling need, so coerce rather than drop.
        raw_payload = event.get("payload")
        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        # The envelope was previously discarded, so a stored record could not be
        # deduplicated, ordered against its producer, or attributed to an engine
        # — everything history replay needs once it stops going through a second
        # projection.
        envelope = {
            "contract_version": 1,
            "engine_kind": "pi_agent_core",
            "event_id": str(event.get("event_id") or "") or None,
            "run_id": str(event.get("run_id") or "") or None,
            "task_attempt_id": str(event.get("task_attempt_id") or "") or None,
            "engine_sequence": sequence if isinstance(sequence, int) else None,
            "occurred_at": _parse_occurred_at(event.get("timestamp")),
        }

        if event_type == "content.delta":
            self._track_delta(payload, envelope)
            live_events.publish(self._task_id, self._live_record(payload))
            return
        if event_type == "content.completed":
            self._open_blocks.pop(_block_key(payload), None)
        elif event_type in TERMINAL_EVENT_TYPES:
            # A block cut short never gets content.completed; its text must be
            # stored before the terminal event so history shows it in order.
            self.flush_open_blocks()

        self._append("agent_event", event_type, payload, envelope)

        if event_type == "run.failed":
            self.append_error(
                code=str(payload.get("error_code") or "pi_runtime_error"),
                message=str(payload.get("message") or "Pi 运行时执行失败"),
                detail=str(payload.get("detail") or ""),
            )

    def append_error(self, *, code: str = "pi_runtime_error", message: str = "请求失败", detail: str = "") -> None:
        """Emit the shared terminal error record.

        Same ``record_type``/shape as ``SdkBlockWriter.append_error`` so error
        handling downstream does not need to know which data plane produced it.
        """
        payload: dict[str, Any] = {
            "code": str(code or "pi_runtime_error"),
            "message": str(message or "请求失败"),
        }
        if detail:
            payload["detail"] = str(detail)
        self._append("error", None, payload)

    def flush_open_blocks(self) -> None:
        """Store each unfinished block's full text as one content.delta row with ``text``.

        ``text`` replaces rather than appends in every reader, so a live client
        that already shows part of the block does not duplicate it.

        Called before a terminal event and once more when the run exits, so any
        exit path keeps the text produced so far. Safe to repeat. Never raises.
        """
        blocks, self._open_blocks = self._open_blocks, {}
        for block in blocks.values():
            try:
                self._store.append_sdk_record(
                    task_id=self._task_id,
                    topic_id=self._topic_id,
                    turn_index=block["turn_index"],
                    record_type="agent_event",
                    event_type="content.delta",
                    data={**block["data"], "text": block["text"]},
                    envelope=block["envelope"],
                )
            except Exception:
                logger.exception("pi_event.flush_open_block_failed task_id=%s", self._task_id)

    def open_blocks_snapshot(self) -> list[dict[str, Any]]:
        """Live records carrying the full text so far (``text``) of each unfinished block."""
        return [
            {**self._live_record({**block["data"], "text": block["text"]}), "turn_index": block["turn_index"]}
            for block in self._open_blocks.values()
        ]

    def _track_delta(self, payload: dict[str, Any], envelope: dict[str, Any]) -> None:
        key = _block_key(payload)
        block = self._open_blocks.get(key)
        if block is None:
            base = {k: v for k, v in payload.items() if k != "delta"}
            block = self._open_blocks[key] = {"turn_index": self._turn_index, "data": base, "text": ""}
        block["text"] += str(payload.get("delta") or "")
        block["envelope"] = envelope

    def _live_record(self, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "turn_index": self._turn_index,
            "record_type": "agent_event",
            "event_type": "content.delta",
            "data": data,
        }

    def _append(self, record_type: str, event_type: str | None, data: dict[str, Any], envelope: dict[str, Any] | None = None) -> None:
        seq_id = self._store.append_sdk_record(
            task_id=self._task_id,
            topic_id=self._topic_id,
            turn_index=self._turn_index,
            record_type=record_type,
            event_type=event_type,
            data=data,
            envelope=envelope,
        )
        # Persistent rows are read back from MySQL by the SSE route; the live
        # channel only needs to know a row up to this id exists.
        if seq_id:
            live_events.publish(self._task_id, {"seq_id": int(seq_id)})


def _block_key(payload: dict[str, Any]) -> tuple[str, str]:
    return (str(payload.get("turn_id") or ""), str(payload.get("content_id") or ""))
