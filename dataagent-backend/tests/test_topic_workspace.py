from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from config import get_settings, update_settings
from core.topic_files import safe_workspace_file
from core.topic_workspace import (
    cleanup_orphan_topic_workspaces,
    delete_topic_workspace,
    prepare_topic_workspace,
    resolve_topic_root,
    resolve_topic_workspace,
)


def _write_skill(root: Path, folder: str) -> None:
    skill_dir = root / folder
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {folder}\n", encoding="utf-8")


def test_resolve_topic_workspace_uses_topic_id_only(monkeypatch, tmp_path: Path):
    runtime_root = tmp_path / "topics"
    workspace = resolve_topic_workspace("topic unsafe/id", runtime_root=runtime_root)
    topic_root = resolve_topic_root("topic unsafe/id", runtime_root=runtime_root)

    # Workspace is the <topic>/workspace subdir; the topic root is its parent.
    assert topic_root == tmp_path / "topics" / "topic-unsafe-id"
    assert workspace == tmp_path / "topics" / "topic-unsafe-id" / "workspace"


def test_resolve_topic_workspace_prefers_container_runtime_root(tmp_path: Path):
    original_runtime_root = get_settings().dataagent_runtime_root
    original_host_root = get_settings().dataagent_host_root
    container_root = tmp_path / "container-runtime"
    host_root = tmp_path / "host-runtime"
    output_file = container_root / "topic_1" / "workspace" / "output" / "report.html"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("<h1>ok</h1>", encoding="utf-8")
    update_settings(
        {
            "dataagent_runtime_root": str(container_root),
            "dataagent_host_root": str(host_root),
        }
    )
    try:
        workspace = resolve_topic_workspace("topic_1")
        resolved_file = safe_workspace_file("topic_1", "output/report.html")
    finally:
        update_settings(
            {
                "dataagent_runtime_root": original_runtime_root,
                "dataagent_host_root": original_host_root,
            }
        )

    assert workspace == container_root / "topic_1" / "workspace"
    assert resolved_file == output_file


def test_resolve_topic_workspace_falls_back_to_host_root_for_local_execution(tmp_path: Path):
    original_runtime_root = get_settings().dataagent_runtime_root
    original_host_root = get_settings().dataagent_host_root
    host_root = tmp_path / "local-runtime"
    update_settings(
        {
            "dataagent_runtime_root": "",
            "dataagent_host_root": str(host_root),
        }
    )
    try:
        workspace = resolve_topic_workspace("topic_1")
    finally:
        update_settings(
            {
                "dataagent_runtime_root": original_runtime_root,
                "dataagent_host_root": original_host_root,
            }
        )

    assert workspace == host_root / "topic_1" / "workspace"


def test_prepare_workspace_removes_reserved_skill_copies_and_preserves_user_files(monkeypatch,tmp_path):
    skills=tmp_path/".claude"/"skills"; _write_skill(skills,"sample")
    monkeypatch.setattr(get_settings(),"skills_root_dir",str(skills))
    root=tmp_path/"topics"; workspace=resolve_topic_workspace("topic-1",runtime_root=root)
    _write_skill(workspace/".claude"/"skills","old-copy")
    (workspace/"output").mkdir(); (workspace/"output"/"report.txt").write_text("keep")
    result=prepare_topic_workspace("topic-1",["sample"],runtime_root=root)
    assert result==workspace
    assert not (workspace/".claude"/"skills").exists()
    assert (workspace/"output"/"report.txt").read_text()=="keep"
    assert (skills/"sample"/"SKILL.md").exists()
    assert not (workspace.parent/"home").exists()


def test_prepare_workspace_rejects_a_skill_source_inside_the_workspace(monkeypatch,tmp_path):
    import pytest
    from core.skill_discovery import SkillDiscoveryError
    workspace=tmp_path/"workspace"; skills=workspace/".claude"/"skills"; _write_skill(skills,"sample")
    monkeypatch.setattr(get_settings(),"skills_root_dir",str(skills))
    with pytest.raises(SkillDiscoveryError,match="cannot be inside"):
        prepare_topic_workspace("topic-1",["sample"],workspace_dir=workspace)
    assert (skills/"sample"/"SKILL.md").exists()


def test_delete_topic_workspace_removes_topic_root_including_logs(tmp_path: Path):
    root = tmp_path / "topics"
    topic_root = root / "topic_1"
    sibling = root / "topic_2"
    # New layout: workspace/, home/, and logs/ are siblings under the topic root.
    (topic_root / "workspace" / ".claude").mkdir(parents=True)
    (topic_root / "home" / ".claude").mkdir(parents=True)
    (topic_root / "logs").mkdir(parents=True)
    (topic_root / "logs" / "task-1.log").write_text("log", encoding="utf-8")
    (sibling / "workspace").mkdir(parents=True)

    deleted = delete_topic_workspace("topic_1", runtime_root=root)

    assert deleted is True
    # Physical delete removes the whole topic root: workspace, home, AND logs.
    assert not topic_root.exists()
    assert sibling.exists()


def test_cleanup_orphan_topic_workspaces_keeps_active_topics(tmp_path: Path):
    root = tmp_path / "topics"
    (root / "topic_keep").mkdir(parents=True)
    (root / "topic_orphan").mkdir(parents=True)

    removed = cleanup_orphan_topic_workspaces({"topic_keep"}, runtime_root=root)

    assert removed == ["topic_orphan"]
    assert (root / "topic_keep").exists()
    assert not (root / "topic_orphan").exists()
