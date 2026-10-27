"""MCP risk is determined by advertised annotations, never a tool name."""
import pytest
from core import permission_gate as pg

@pytest.mark.parametrize("hints,write,critical", [
    (None, True, False), ({}, True, False),
    ({"readOnlyHint": False}, True, False),
    ({"readOnlyHint": True}, False, False),
    ({"readOnlyHint": True, "destructiveHint": True}, True, True),
    ({"destructiveHint": True}, True, True),
])
@pytest.mark.parametrize("name", ["mcp__files__read", "mcp__db__arbitrary"])
def test_annotations_control_all_mcp_tools(hints, write, critical, name):
    assert pg.is_write_tool(name, hints) == write
    assert pg.is_high_risk_tool(name, hints) == critical
    assert pg.plan_denies_tool(name, hints) == write
    assert pg.requires_confirmation(name, "default", hints) == write
    assert pg.requires_confirmation(name, "acceptEdits", hints) == critical
    assert not pg.requires_confirmation(name, "bypassPermissions", hints)
    assert not pg.requires_confirmation(name, "plan", hints)

@pytest.mark.parametrize("name", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_plan_denies_builtin_mutations(name):
    assert pg.plan_denies_tool(name)
    assert not pg.plan_denies_tool("Read")

def test_card_metadata_is_not_forwarded():
    assert pg.strip_card_annotations({"title": "confirm", "summary": "change", "params": {"x": 1}}) == {"params": {"x": 1}}
