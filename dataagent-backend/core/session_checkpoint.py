"""Transactional session boundaries, mixed into the topic/task store.

Only completed SDK segments are resumable. Running tasks with unknown external
side effects are never treated as resumable checkpoints.
"""
from __future__ import annotations

import json
from typing import Any


class SessionCheckpointStore:
    def get_session_checkpoint(self, topic_id: str) -> dict[str, Any] | None:
        conn = self._connect(database=self._schema_name())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM da_agent_session_checkpoint WHERE topic_id=%s", (topic_id,))
                row = cur.fetchone()
                if not row:
                    return None
                return {"task_id": row["task_id"], "version": int(row["version"]),
                        "header": json.loads(row["header_json"]), "entries": json.loads(row["entries_json"]),
                        "leaf_id": row["leaf_id"], "state": json.loads(row["state_json"])}
        finally:
            conn.close()

    def commit_session_segment(self, *, task_id: str, checkpoint: dict[str, Any], expected_version: int,
                               state: dict[str, Any], card: dict[str, Any] | None = None,
                               turn_index: int = 0) -> None:
        serialized = json.dumps(checkpoint, ensure_ascii=False)
        if len(serialized.encode()) > 8 * 1024 * 1024:
            raise ValueError("SESSION_SNAPSHOT_TOO_LARGE")
        if not isinstance(checkpoint.get("header"), dict) or not isinstance(checkpoint.get("entries"), list):
            raise ValueError("Invalid session checkpoint")
        conn = self._connect(database=self._schema_name())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT topic_id,task_status,cancel_requested_at FROM da_agent_task WHERE task_id=%s FOR UPDATE", (task_id,))
                task = cur.fetchone()
                if not task or task["task_status"] != "running" or task["cancel_requested_at"]:
                    raise ValueError("Session task is no longer running")
                topic_id = task["topic_id"]
                cur.execute("SELECT version FROM da_agent_session_checkpoint WHERE topic_id=%s FOR UPDATE", (topic_id,))
                previous = cur.fetchone()
                if int((previous or {}).get("version", 0)) != expected_version:
                    raise ValueError("Stale session checkpoint")
                cur.execute("""INSERT INTO da_agent_session_checkpoint
                    (topic_id,task_id,header_json,entries_json,leaf_id,version,state_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE task_id=VALUES(task_id),header_json=VALUES(header_json),
                    entries_json=VALUES(entries_json),leaf_id=VALUES(leaf_id),version=VALUES(version),state_json=VALUES(state_json)""",
                    (topic_id, task_id, json.dumps(checkpoint["header"], ensure_ascii=False),
                     json.dumps(checkpoint["entries"], ensure_ascii=False), checkpoint.get("leaf_id"),
                     expected_version + 1, json.dumps(state, ensure_ascii=False)))
                if card:
                    cur.execute("""INSERT INTO da_agent_sdk_record
                        (topic_id,task_id,turn_index,record_type,event_type,data)
                        VALUES (%s,%s,%s,%s,NULL,%s)""",
                        (topic_id,task_id,turn_index,card["record_type"],json.dumps(card["data"],ensure_ascii=False)))
                    status = "waiting_input" if card["record_type"] == "question_request" else "waiting_permission"
                    cur.execute("UPDATE da_agent_task SET task_status=%s,heartbeat_at=NULL WHERE task_id=%s", (status,task_id))
                    cur.execute("UPDATE da_agent_topic SET current_task_status=%s WHERE topic_id=%s AND current_task_id=%s", (status,topic_id,task_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def claim_confirmed_operation(self, *, topic_id: str, task_id: str, request_id: str) -> dict[str, Any]:
        """Claim once before launching the segment; an interrupted claim is unknown."""
        conn = self._connect(database=self._schema_name())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT state_json FROM da_agent_session_checkpoint WHERE topic_id=%s AND task_id=%s FOR UPDATE", (topic_id,task_id))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Session checkpoint missing")
                state = json.loads(row["state_json"])
                interaction = state.get("interaction") or {}
                if interaction.get("request_id") != request_id or (interaction.get("result") or {}).get("decision") != "allow":
                    raise ValueError("Operation approval mismatch")
                status = interaction.get("operation_status", "ready")
                if status == "executing":
                    raise ValueError("OPERATION_RESULT_UNKNOWN: check external state before retrying")
                if status == "done":
                    return interaction
                interaction["operation_status"] = "executing"
                cur.execute("UPDATE da_agent_session_checkpoint SET state_json=%s WHERE topic_id=%s", (json.dumps(state,ensure_ascii=False),topic_id))
            conn.commit()
            return interaction
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_confirmed_operation_result(self, *, topic_id: str, task_id: str, request_id: str, result: Any) -> None:
        conn = self._connect(database=self._schema_name())
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT state_json FROM da_agent_session_checkpoint WHERE topic_id=%s AND task_id=%s FOR UPDATE", (topic_id,task_id))
                row = cur.fetchone()
                if not row:
                    raise ValueError("Operation checkpoint missing")
                state = json.loads(row["state_json"])
                interaction = state.get("interaction") or {}
                if interaction.get("request_id") != request_id or interaction.get("operation_status") != "executing":
                    raise ValueError("Operation claim mismatch")
                interaction.update(operation_status="done", operation_result=result)
                cur.execute("UPDATE da_agent_session_checkpoint SET state_json=%s WHERE topic_id=%s", (json.dumps(state,ensure_ascii=False),topic_id))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
