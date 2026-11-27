"""Unit and API contract tests for core.time_utils, AwareDatetime inputs, and schedule TZ defaults."""
from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import Settings
from core import time_utils
from core.accounts import AccountContext, ROLE_ADMIN, RoleRef, User
from main import app


def test_to_rfc3339_formats_aware_datetimes_and_rejects_naive():
    assert time_utils.to_rfc3339(None) == ""

    utc_dt = datetime(2026, 11, 26, 9, 15, 30, 123456, tzinfo=UTC)
    assert time_utils.to_rfc3339(utc_dt) == "2026-11-26T09:15:30Z"

    shanghai = timezone(timedelta(hours=8))
    assert time_utils.to_rfc3339(datetime(2026, 11, 26, 17, 15, 30, tzinfo=shanghai)) == "2026-11-26T09:15:30Z"

    pst = timezone(timedelta(hours=-8))
    assert time_utils.to_rfc3339(datetime(2026, 11, 26, 1, 15, 30, tzinfo=pst)) == "2026-11-26T09:15:30Z"

    with pytest.raises(ValueError, match="Naive datetime"):
        time_utils.to_rfc3339(datetime(2026, 11, 26, 9, 15, 30))

    with pytest.raises(TypeError):
        time_utils.to_rfc3339("2026-11-26T09:15:30Z")  # type: ignore[arg-type]


def test_business_day_bounds_shanghai_and_los_angeles_including_dst(monkeypatch):
    monkeypatch.setattr(time_utils, "get_settings", lambda: SimpleNamespace(dataagent_timezone="Asia/Shanghai"))
    start, end = time_utils.business_day_bounds(date(2026, 11, 26))
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.astimezone(UTC) == datetime(2026, 11, 25, 16, 0, 0, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2026, 11, 26, 16, 0, 0, tzinfo=UTC)

    # America/Los_Angeles spring-forward day (2026-03-08) is 23 hours long.
    monkeypatch.setattr(time_utils, "get_settings", lambda: SimpleNamespace(dataagent_timezone="America/Los_Angeles"))
    dst_start, dst_end = time_utils.business_day_bounds(date(2026, 3, 8))
    assert dst_start.astimezone(UTC) == datetime(2026, 3, 8, 8, 0, 0, tzinfo=UTC)
    assert dst_end.astimezone(UTC) == datetime(2026, 3, 9, 7, 0, 0, tzinfo=UTC)
    assert (dst_end.astimezone(UTC) - dst_start.astimezone(UTC)) == timedelta(hours=23)


def test_settings_validates_dataagent_timezone(monkeypatch):
    monkeypatch.delenv("DATAAGENT_TIMEZONE", raising=False)
    assert Settings(_env_file=None).dataagent_timezone == "Asia/Shanghai"

    monkeypatch.setenv("DATAAGENT_TIMEZONE", "America/New_York")
    assert Settings(_env_file=None).dataagent_timezone == "America/New_York"

    for bad in ("", "   ", "Invalid/Zone", "UTC+8"):
        monkeypatch.setenv("DATAAGENT_TIMEZONE", bad)
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


def test_admin_time_filters_require_aware_datetime_and_forward_aware_bounds(monkeypatch):
    from api import admin_routes
    from core.accounts import require_admin

    admin_user = User(
        id=1,
        username="admin",
        display_name="Admin",
        is_active=True,
        roles=[RoleRef(id=1, role_key=ROLE_ADMIN, name="管理员", is_builtin=True)],
    )
    app.dependency_overrides[require_admin] = lambda: admin_user
    captured: list[tuple[str, dict]] = []

    class FakeStore:
        def list_message_feedback(self, **kwargs):
            captured.append(("feedback", kwargs))
            return {"items": [], "total": 0, "likes": 0, "dislikes": 0, "page": 1, "page_size": 20}

        def admin_list_topics(self, **kwargs):
            captured.append(("topics", kwargs))
            return {"items": [], "total": 0, "page": 1, "page_size": 20}

    monkeypatch.setattr(admin_routes, "get_topic_task_store", lambda: FakeStore())
    try:
        client = TestClient(app)

        # Missing timezone offset is rejected by Pydantic AwareDatetime.
        for path in ("/api/v1/nl2sql-admin/feedback", "/api/v1/nl2sql-admin/topics"):
            resp_naive = client.get(path, params={"start": "2026-11-26T00:00:00"})
            assert resp_naive.status_code == 422

            resp_date_only = client.get(path, params={"start": "2026-11-26"})
            assert resp_date_only.status_code == 422

            resp_ok = client.get(
                path,
                params={"start": "2026-11-25T16:00:00Z", "end": "2026-11-26T16:00:00+00:00"},
            )
            assert resp_ok.status_code == 200

        assert len(captured) == 2
        for _, kwargs in captured:
            assert kwargs["start"] == datetime(2026, 11, 25, 16, 0, 0, tzinfo=UTC)
            assert kwargs["end"] == datetime(2026, 11, 26, 16, 0, 0, tzinfo=UTC)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_schedule_create_uses_config_timezone_and_update_preserves_stored_timezone(monkeypatch):
    from api import routes

    admin_user = User(
        id=1,
        username="admin",
        display_name="Admin",
        is_active=True,
        roles=[RoleRef(id=1, role_key=ROLE_ADMIN, name="管理员", is_builtin=True)],
    )
    ctx = AccountContext(user=admin_user, source="web", website_id="")

    stored: dict[str, dict] = {}
    calls: dict[str, list] = {"create": [], "update": []}

    class FakeStore:
        def get_topic(self, topic_id, context=None):
            return {"topic_id": topic_id, "agent_id": "agent_default", "user_id": 1, "source": "web"}

        def create_message_schedule(self, **kwargs):
            calls["create"].append(kwargs)
            rec = {
                "schedule_id": "sch_1",
                "topic_id": kwargs["topic_id"],
                "name": kwargs["name"],
                "message_type": kwargs["message_type"],
                "message_content": kwargs["message_content"],
                "cron_expr": kwargs["cron_expr"],
                "enabled": kwargs["enabled"],
                "timezone": kwargs["timezone"],
                "next_run_at": time_utils.to_rfc3339(kwargs["next_run_at"]) if kwargs["next_run_at"] else None,
                "created_at": "2026-11-26T00:00:00Z",
                "updated_at": "2026-11-26T00:00:00Z",
            }
            stored["sch_1"] = rec
            return rec

        def get_message_schedule(self, schedule_id, context=None):
            return stored.get(schedule_id)

        def update_message_schedule(self, **kwargs):
            calls["update"].append(kwargs)
            rec = {
                **stored[kwargs["schedule_id"]],
                "name": kwargs["name"],
                "cron_expr": kwargs["cron_expr"],
                "timezone": kwargs["timezone"],
                "next_run_at": time_utils.to_rfc3339(kwargs["next_run_at"]) if kwargs["next_run_at"] else None,
            }
            stored[kwargs["schedule_id"]] = rec
            return rec

    monkeypatch.setattr(routes, "_get_store", lambda: FakeStore())
    monkeypatch.setattr(routes, "current_account_context", lambda _req: ctx)
    monkeypatch.setattr(routes, "require_agent_access", lambda *a, **kw: None)
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(dataagent_timezone="America/Los_Angeles"))

    client = TestClient(app)
    create_resp = client.post(
        "/api/v1/nl2sql/message-schedule",
        json={
            "topic_id": "topic_1",
            "name": "Daily report",
            "message_type": "text",
            "message_content": "hello",
            "cron_expr": "0 9 * * *",
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    assert create_resp.json()["timezone"] == "America/Los_Angeles"
    assert calls["create"][0]["next_run_at"].tzinfo is not None

    # Change config default; updating without timezone must preserve the stored timezone.
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(dataagent_timezone="Asia/Shanghai"))
    update_resp = client.put(
        "/api/v1/nl2sql/message-schedule/sch_1",
        json={
            "topic_id": "topic_1",
            "name": "Daily report renamed",
            "message_type": "text",
            "message_content": "hello",
            "cron_expr": "0 10 * * *",
            "enabled": True,
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["timezone"] == "America/Los_Angeles"
    assert calls["update"][0]["timezone"] == "America/Los_Angeles"
