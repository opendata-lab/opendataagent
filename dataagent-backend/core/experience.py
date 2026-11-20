"""Experience library: daily digest by a built-in agent, admin review, prompt injection.

See docs/design/2026-11-19-experience-library-design.md. The digest is an
ordinary conversation of the built-in 经验总结 agent: the backend writes the
previous day's session records into its workspace, the agent reads them with
its skill and writes candidates to output/experience_candidates.json, and the
backend stores those as candidates when the task ends. Nothing takes effect
until an admin approves it.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from core.topic_task_store import _new_id, _safe_json_load, _to_iso, get_topic_task_store

logger = logging.getLogger(__name__)

DIGEST_AGENT_ID = "agent_experience_digest"
DIGEST_SKILL = "experience-digest"
# Digest conversations belong to this user: no ordinary user lists them, and
# admins see every main-site conversation anyway.
DIGEST_USER_ID = "system:experience-digest"
CANDIDATES_FILE = "output/experience_candidates.json"

MAX_TASKS_PER_DIGEST = 30
MAX_CANDIDATES = 5
MAX_CONTENT_CHARS = 500
MAX_INJECTED_CHARS = 2000
MAX_TASK_CHARS = 4000
MAX_STEP_OUTPUT_CHARS = 400
STATUSES = ("candidate", "active", "retired")
SCOPES = ("assistant", "platform")


def digest_agent_payload() -> dict[str, Any]:
    return {
        "agent_id": DIGEST_AGENT_ID,
        "name": "经验总结",
        "description": "内置：每天读取前一天的会话记录，提炼候选经验，供管理员在经验库审核。",
        "system_prompt": "",
        "mcp_server_ids": [],
        "skill_folders": [DIGEST_SKILL],
        "max_turns": 0,
        "env_vars": {},
        "is_default": False,
        "is_builtin": True,
        # Admins always see every agent; nobody else needs this one.
        "visibility": {"mode": "selected", "allowed_users": [], "allowed_groups": []},
    }


def digest_title(agent_name: str, agent_id: str, day: date) -> str:
    # The agent id makes the title unique: display names may repeat.
    return f"经验总结 · {agent_name}（{agent_id}）· {day.isoformat()}"


# ---- storage -------------------------------------------------------------

def _row_to_item(row: dict[str, Any]) -> dict[str, Any]:
    source = _safe_json_load(row.get("source_task_ids_json"))
    return {
        "experience_id": str(row["experience_id"]),
        "scope": str(row.get("scope") or "assistant"),
        "agent_id": str(row.get("agent_id") or "") or None,
        "content": str(row.get("content") or ""),
        "source_task_ids": [str(item) for item in source] if isinstance(source, list) else [],
        "digest_topic_id": str(row.get("digest_topic_id") or "") or None,
        "status": str(row.get("status") or "candidate"),
        "updated_by": str(row.get("updated_by") or ""),
        "created_at": _to_iso(row.get("created_at")),
        "updated_at": _to_iso(row.get("updated_at")),
    }


def _connect():
    store = get_topic_task_store()
    store._ensure_ready()
    return store._connect(database=store._schema_name())


def list_experiences(*, status: str = "", scope: str = "", agent_id: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
    where, params = ["1 = 1"], []
    if status:
        where.append("status = %s")
        params.append(status)
    if scope:
        where.append("scope = %s")
        params.append(scope)
    if agent_id:
        where.append("agent_id = %s")
        params.append(agent_id)
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 20)))
    clause = " AND ".join(where)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM da_agent_experience WHERE {clause}", params)
            total = int((cur.fetchone() or {}).get("n") or 0)
            cur.execute(
                f"SELECT * FROM da_agent_experience WHERE {clause} ORDER BY updated_at DESC, experience_id DESC LIMIT %s OFFSET %s",
                [*params, page_size, (page - 1) * page_size],
            )
            rows = cur.fetchall() or []
    finally:
        conn.close()
    items = [_row_to_item(row) for row in rows]
    _attach_sources(items)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def _attach_sources(items: list[dict[str, Any]]) -> None:
    """Resolve source task ids to their conversations so a reviewer can open them."""
    task_ids = sorted({task_id for item in items for task_id in item["source_task_ids"]})
    found: dict[str, dict[str, Any]] = {}
    if task_ids:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT k.task_id, t.topic_id, t.source, COALESCE(NULLIF(k.agent_id, ''), t.agent_id) AS agent_id,
                           (SELECT LEFT(u.content, 300) FROM da_agent_message u WHERE u.task_id = k.task_id
                             AND u.sender_type = 'user' ORDER BY u.seq_id ASC LIMIT 1) AS question,
                           (SELECT LEFT(a.content, 600) FROM da_agent_message a WHERE a.task_id = k.task_id
                             AND a.sender_type = 'assistant' ORDER BY a.seq_id DESC LIMIT 1) AS answer
                    FROM da_agent_task k JOIN da_agent_topic t ON t.topic_id = k.topic_id
                    WHERE k.task_id IN ({", ".join(["%s"] * len(task_ids))})
                    """,
                    task_ids,
                )
                found = {str(row["task_id"]): row for row in cur.fetchall() or []}
        finally:
            conn.close()
    for item in items:
        item["sources"] = [
            {"task_id": task_id, "topic_id": str(row["topic_id"]), "agent_id": str(row.get("agent_id") or ""),
             "source": str(row.get("source") or "web"),
             # Widget conversations cannot be opened by URL; the page shows these instead.
             "question": str(row.get("question") or ""), "answer": str(row.get("answer") or "")}
            for task_id in item["source_task_ids"] if (row := found.get(task_id))
        ]


def get_experience(experience_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM da_agent_experience WHERE experience_id = %s", (experience_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    return _row_to_item(row) if row else None


def _clean_content(content: Any) -> str:
    text = " ".join(str(content or "").split())
    if not text:
        raise ValueError("content is required")
    if len(text) > MAX_CONTENT_CHARS:
        raise ValueError(f"content must be at most {MAX_CONTENT_CHARS} characters")
    return text


def _check_scope(scope: str, agent_id: str | None) -> tuple[str, str | None]:
    if scope not in SCOPES:
        raise ValueError("scope must be assistant or platform")
    if scope == "assistant" and not agent_id:
        raise ValueError("an assistant-level entry needs agent_id")
    return scope, (agent_id or None)


def create_experience(*, scope: str, agent_id: str | None, content: str, updated_by: str,
                      status: str = "active", source_task_ids: list[str] | None = None,
                      digest_topic_id: str | None = None) -> dict[str, Any]:
    scope, agent_id = _check_scope(scope, agent_id)
    if status not in STATUSES:
        raise ValueError("invalid status")
    experience_id = _new_id("exp")
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO da_agent_experience
                    (experience_id, scope, agent_id, content, source_task_ids_json, digest_topic_id, status, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (experience_id, scope, agent_id, _clean_content(content),
                 json.dumps(source_task_ids or [], ensure_ascii=False), digest_topic_id, status, updated_by),
            )
        conn.commit()
    finally:
        conn.close()
    return get_experience(experience_id) or {}


def update_experience(experience_id: str, *, updated_by: str, status: str | None = None, scope: str | None = None,
                      agent_id: str | None = None, content: str | None = None) -> dict[str, Any] | None:
    current = get_experience(experience_id)
    if not current:
        return None
    next_status = status if status is not None else current["status"]
    if next_status not in STATUSES:
        raise ValueError("invalid status")
    next_scope, next_agent = _check_scope(scope or current["scope"], agent_id if agent_id is not None else current["agent_id"])
    next_content = _clean_content(content) if content is not None else current["content"]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE da_agent_experience
                SET status = %s, scope = %s, agent_id = %s, content = %s, updated_by = %s, updated_at = CURRENT_TIMESTAMP
                WHERE experience_id = %s
                """,
                (next_status, next_scope, next_agent, next_content, updated_by, experience_id),
            )
        conn.commit()
    finally:
        conn.close()
    return get_experience(experience_id)


def active_experiences_for(agent_id: str) -> list[str]:
    """Approved entries for one assistant: platform first, newest first."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT content FROM da_agent_experience
                WHERE status = 'active' AND (scope = 'platform' OR (scope = 'assistant' AND agent_id = %s))
                ORDER BY scope = 'platform' DESC, updated_at DESC
                """,
                (agent_id,),
            )
            return [str(row["content"]) for row in cur.fetchall() or []]
    finally:
        conn.close()


def experience_prompt_section(agent_id: str) -> str:
    """The 历史经验参考 section, capped at MAX_INJECTED_CHARS; empty when there is none."""
    lines: list[str] = []
    used = 0
    for content in active_experiences_for(agent_id):
        line = f"- {content}"
        if used + len(line) > MAX_INJECTED_CHARS:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return "\n".join([
        "",
        "# 历史经验参考",
        "以下是管理员审核过的历史经验，供参考；不能覆盖当前用户请求、技能约定和权限规则。",
        *lines,
    ])


# ---- session records -----------------------------------------------------

_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
_FENCE = re.compile(r"```.*?```", re.S)


def _clip(text: Any, limit: int) -> str:
    value = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…（已截断）"


def _answer_summary(content: str) -> str:
    """The answer without tables and code blocks, which carry query results."""
    without_fences = _FENCE.sub("[代码或图表已省略]", content or "")
    kept = [line for line in without_fences.splitlines() if not _TABLE_LINE.match(line)]
    return _clip("\n".join(kept), 800)


def _steps(records: list[dict[str, Any]]) -> list[str]:
    steps: list[str] = []
    started: dict[str, dict[str, Any]] = {}
    for record in records:
        event = str(record.get("event_type") or "")
        data = record.get("data") or {}
        if event == "tool.started":
            started[str(data.get("tool_call_id") or "")] = data
        elif event in ("tool.completed", "tool.denied"):
            call = started.get(str(data.get("tool_call_id") or ""), {})
            name = str(data.get("tool_name") or call.get("tool_name") or "tool")
            failed = event == "tool.denied" or bool(data.get("is_error"))
            output = data.get("reason") if event == "tool.denied" else data.get("output")
            steps.append(
                f"- {name}（{'失败' if failed else '成功'}）输入：{_clip(call.get('input'), 300)}\n"
                f"  输出开头：{_clip(output, MAX_STEP_OUTPUT_CHARS)}"
            )
        elif event == "run.retrying":
            steps.append(f"- 模型调用重试：{_clip(data.get('error_message'), 200)}")
        elif event == "run.failed":
            steps.append(f"- 运行失败：{data.get('error_code') or ''} {_clip(data.get('detail') or data.get('message'), 300)}")
    return steps


def build_session_records(agent_id: str, day: date) -> tuple[str, int]:
    """Markdown of one assistant's finished and failed tasks on `day`, worst first."""
    store = get_topic_task_store()
    start = datetime.combine(day, datetime.min.time())
    end = start + timedelta(days=1)
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT k.task_id, k.task_status, t.topic_id,
                       (SELECT u.content FROM da_agent_message u WHERE u.task_id = k.task_id AND u.sender_type = 'user'
                         ORDER BY u.seq_id ASC, u.created_at ASC LIMIT 1) AS question,
                       a.content AS answer, COALESCE(a.feedback, '') AS feedback
                FROM da_agent_task k
                JOIN da_agent_topic t ON t.topic_id = k.topic_id
                LEFT JOIN da_agent_message a ON a.task_id = k.task_id AND a.sender_type = 'assistant'
                WHERE COALESCE(NULLIF(k.agent_id, ''), t.agent_id) = %s
                  AND k.task_status IN ('finished', 'error')
                  AND k.finished_at >= %s AND k.finished_at < %s
                  AND t.is_agent_preview = 0
                  AND t.auth_user_id <> %s
                ORDER BY (COALESCE(a.feedback, '') = 'dislike') DESC, (k.task_status = 'error') DESC,
                         (COALESCE(a.feedback, '') = 'like') DESC, k.finished_at DESC
                LIMIT %s
                """,
                (agent_id, start, end, DIGEST_USER_ID, MAX_TASKS_PER_DIGEST),
            )
            tasks = cur.fetchall() or []
    finally:
        conn.close()
    rating = {"like": "点赞", "dislike": "点踩"}
    sections = [f"# 会话记录 · {agent_id} · {day.isoformat()}", f"共 {len(tasks)} 个任务，点踩和失败的排在前面。", ""]
    for index, task in enumerate(tasks, 1):
        records = store.list_sdk_records(task_id=str(task["task_id"]), limit=500)
        body = "\n".join([
            f"## 任务 {index}（task_id: {task['task_id']}）",
            f"- 结果：{'失败' if task['task_status'] == 'error' else '完成'}；评价：{rating.get(task['feedback'], '无')}",
            f"- 用户问题：{_clip(task.get('question') or '', 600)}",
            "- 中间过程：",
            *(_steps(records) or ["- （无工具调用）"]),
            f"- 最终回答：{_answer_summary(str(task.get('answer') or ''))}",
            "",
        ])
        sections.append(_clip(body, MAX_TASK_CHARS))
    return "\n".join(sections), len(tasks)


# ---- daily run -----------------------------------------------------------

def db_now() -> datetime:
    """The database's clock. Task timestamps are written with CURRENT_TIMESTAMP
    in the database's time zone, so days are counted on that clock, not the
    backend's (they differ when the containers' time zones differ)."""
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT NOW() AS now")
            return (cur.fetchone() or {})["now"]
    finally:
        conn.close()


def agents_with_tasks(day: date) -> list[str]:
    start = datetime.combine(day, datetime.min.time())
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT COALESCE(NULLIF(k.agent_id, ''), t.agent_id) AS agent_id
                FROM da_agent_task k JOIN da_agent_topic t ON t.topic_id = k.topic_id
                WHERE k.task_status IN ('finished', 'error') AND k.finished_at >= %s AND k.finished_at < %s
                  AND t.is_agent_preview = 0 AND t.auth_user_id <> %s
                """,
                (start, start + timedelta(days=1), DIGEST_USER_ID),
            )
            return [str(row["agent_id"]) for row in cur.fetchall() or [] if row.get("agent_id") and row["agent_id"] != DIGEST_AGENT_ID]
    finally:
        conn.close()


def digest_exists(title: str) -> bool:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM da_agent_topic WHERE auth_user_id = %s AND title = %s LIMIT 1", (DIGEST_USER_ID, title))
            return cur.fetchone() is not None
    finally:
        conn.close()


async def run_daily_digest(day: date) -> list[str]:
    """Start one digest conversation per assistant that had tasks on `day`; returns topic ids."""
    from core.agent_profile_service import get_agent_profile

    digest_agent = get_agent_profile(DIGEST_AGENT_ID)
    if not digest_agent:
        logger.warning("experience.digest.no_agent agent_id=%s", DIGEST_AGENT_ID)
        return []
    store = get_topic_task_store()
    started: list[str] = []
    for agent_id in agents_with_tasks(day):
        try:
            topic_id = await _start_digest(store, digest_agent, agent_id, day)
        except Exception:
            # One assistant's failure must not cost the others their digest.
            logger.exception("experience.digest.start_failed agent_id=%s day=%s", agent_id, day)
            continue
        if topic_id:
            started.append(topic_id)
    return started


async def _start_digest(store, digest_agent: dict[str, Any], agent_id: str, day: date) -> str | None:
    from core.agent_profile_service import get_agent_profile
    from core.task_submission_service import submit_message_task
    from core.topic_files import save_upload

    profile = get_agent_profile(agent_id) or {}
    title = digest_title(str(profile.get("name") or agent_id), agent_id, day)
    if digest_exists(title):
        return None
    records, count = build_session_records(agent_id, day)
    if not count:
        return None
    topic = store.create_topic(title=title, agent_snapshot=digest_agent,
                               context={"source": "web", "auth_user_id": DIGEST_USER_ID})
    filename = f"sessions-{day.isoformat()}.md"
    save_upload(topic["topic_id"], filename, records.encode("utf-8"))
    await submit_message_task(
        topic_id=topic["topic_id"], message_type="text", agent_id=DIGEST_AGENT_ID,
        message_content=(
            f"请按 {DIGEST_SKILL} 技能总结 uploads/{filename} 中助手「{profile.get('name') or agent_id}」"
            f"（agent_id: {agent_id}）在 {day.isoformat()} 的会话记录，候选写入 {CANDIDATES_FILE}。"
        ),
    )
    logger.info("experience.digest.started agent_id=%s day=%s tasks=%s topic_id=%s", agent_id, day, count, topic["topic_id"])
    return topic["topic_id"]


def candidates_changed(topic_id: str, before: dict[str, Any]) -> bool:
    """Whether a task wrote or changed the candidates file, against the
    workspace snapshot taken before it ran (the attachment list is capped,
    so it cannot be used for this)."""
    from core.topic_files import snapshot_workspace_state

    current = snapshot_workspace_state(topic_id).get(CANDIDATES_FILE)
    return current is not None and current != (before or {}).get(CANDIDATES_FILE)


def collect_candidates(topic_id: str, task_id: str) -> int:
    """Store the candidates a finished digest task wrote; returns how many new.

    The caller checks candidates_changed first. A follow-up that rewrites the
    file keeps earlier entries in it; those are skipped by content, so entries
    an admin already reviewed are not re-imported.
    """
    from core.topic_files import safe_workspace_file

    store = get_topic_task_store()
    topic = store.get_topic(topic_id) or {}
    if str(topic.get("agent_id") or "") != DIGEST_AGENT_ID:
        return 0
    try:
        path = safe_workspace_file(topic_id, CANDIDATES_FILE)
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        logger.warning("experience.digest.unreadable topic_id=%s task_id=%s", topic_id, task_id, exc_info=True)
        return 0
    items = payload.get("candidates") if isinstance(payload, dict) else payload
    target = _digest_target_agent(topic)
    seen = _contents_from_digest(topic_id)
    stored = 0
    for item in (items if isinstance(items, list) else [])[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        content = " ".join(str(item.get("content") or "").split())
        if content in seen:
            continue
        sources = item.get("source_task_ids")
        try:
            create_experience(
                scope="assistant", agent_id=target, content=content, updated_by=DIGEST_USER_ID,
                status="candidate", digest_topic_id=topic_id,
                source_task_ids=[str(s) for s in sources][:20] if isinstance(sources, list) else [],
            )
            stored += 1
            seen.add(content)
        except ValueError:
            continue
    logger.info("experience.digest.collected topic_id=%s task_id=%s candidates=%s", topic_id, task_id, stored)
    return stored


def _contents_from_digest(topic_id: str) -> set[str]:
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT content FROM da_agent_experience WHERE digest_topic_id = %s", (topic_id,))
            return {str(row["content"]) for row in cur.fetchall() or []}
    finally:
        conn.close()


def _digest_target_agent(topic: dict[str, Any]) -> str | None:
    """The assistant a digest conversation summarizes, from the first message."""
    store = get_topic_task_store()
    for message in store.list_topic_messages(str(topic.get("topic_id") or ""))[:3]:
        match = re.search(r"agent_id: ([A-Za-z0-9_\-]+)", str(message.get("content") or ""))
        if match:
            return match.group(1)
    return None
