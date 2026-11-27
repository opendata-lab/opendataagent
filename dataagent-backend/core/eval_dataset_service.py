"""Evaluation dataset import/export service.

Uses ``eval_contract`` for validation and canonical serialization (matching
the ``tools/dataagent-evals/dataset/manage.py`` contract byte-for-byte).
"""
from __future__ import annotations

import uuid
from typing import Any

from core.eval_contract import canonical_bytes, parse_jsonl, validate
from core.eval_store import get_eval_store


def import_dataset(
    name: str, data: bytes, *, user_id: int | None = None, created_by: str = ""
) -> dict[str, Any]:
    """Parse JSONL bytes, validate, persist dataset + cases. Returns dataset row."""
    rows = parse_jsonl(data)
    if not rows:
        raise ValueError("empty dataset")
    result = validate(rows)
    if not result["valid"]:
        raise ValueError("; ".join(result["errors"][:20]))

    dataset_id = f"ds-{uuid.uuid4().hex[:12]}"
    store = get_eval_store()
    ds = store.create_dataset(
        dataset_id=dataset_id,
        name=name,
        case_count=result["case_count"],
        dataset_hash=result["dataset_hash"],
        user_id=user_id,
        created_by=created_by,
    )
    store.replace_cases(dataset_id, rows, result["dataset_hash"])
    return ds


def export_dataset(dataset_id: str) -> tuple[str, bytes]:
    """Return ``(filename, jsonl_bytes)`` for download."""
    store = get_eval_store()
    ds = store.get_dataset(dataset_id)
    if not ds:
        raise ValueError(f"dataset not found: {dataset_id}")
    rows = store.get_case_full_json(dataset_id)
    data = canonical_bytes(rows)
    safe_name = str(ds.get("name") or dataset_id).replace("/", "_").replace(" ", "_")
    return f"{safe_name}.jsonl", data
