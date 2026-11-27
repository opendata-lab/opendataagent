from __future__ import annotations

from typing import Literal

CancelReason = Literal["user_cancel", "runner_stop"]
PARKED_TASK_STATUSES = {"waiting_permission", "waiting_input"}
