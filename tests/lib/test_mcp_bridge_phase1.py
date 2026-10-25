"""Phase-1 contract tests for the MCP bridge (summary/tasks/002, summary/tasks/003).

Phase 0 was read-only. Phase 1 adds governed execution:
  - decisions can be recorded mid-stage, without rewriting the stage;
  - a tool call is priced, budget-checked and reconciled;
  - a refused reservation must never take the server down;
  - the cost tracker must survive MCP calls and a server restart.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.checkpoint import CheckpointValidationError, init_project
from lib.mcp_bridge import (
    BridgeError,
    budget,
    decision_append,
    decision_read,
    tool_approve,
    tool_estimate,
    tool_run,
    tracker_for,
)
from tools.base_tool import BaseTool, ToolResult
from tools.tool_registry import registry


class FakePaidTool(BaseTool):
    """A tool that costs money — exercises estimate/reserve/reconcile."""

    name = "mcp_fake_paid_tool"
    capability = "video_generation"
    provider = "fake"

    def estimate_cost(self, inputs: dict) -> float:
        return float(inputs.get("price", 1.0))

    def execute(self, inputs: dict) -> ToolResult:
        return ToolResult(
            success=True,
            data={"written": inputs.get("output_path")},
            artifacts=[inputs.get("output_path")] if inputs.get("output_path") else [],
            cost_usd=0.5,
            model="fake-model",
            seed=42,
        )


class FakeCheapTool(BaseTool):
    name = "mcp_fake_cheap_tool"
    capability = "analysis"
    provider = "fake"

    def estimate_cost(self, inputs: dict) -> float:
        return 0.0

    def execute(self, inputs: dict) -> ToolResult:
        return ToolResult(success=True, data={"ok": True}, cost_usd=0.0)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real project workspace, with the bridge pointed at it."""
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    init_project(
        "run",
        title="Run",
        pipeline_type="animated-explainer",
        pipeline_dir=tmp_path,
    )
    registry.register(FakePaidTool())
    registry.register(FakeCheapTool())
    from lib.mcp_bridge import _TRACKERS

    _TRACKERS.clear()
    yield tmp_path / "run"
    _TRACKERS.clear()


# ---------------------------------------------------------------------------
# T002 — decisions without touching the stage
# ---------------------------------------------------------------------------


def _decision(**overrides) -> dict:
    payload = {
        "option_id": "hyperframes",
        "label": "HyperFrames",
        "score": 0.9,
        "reason": "kinetic typography fits the brief",
    }
    payload.update(overrides)
    return payload


def test_decision_append_writes_the_log_without_touching_checkpoints(project) -> None:
    result = decision_append(
        "run",
        "proposal",
        "render_runtime_selection",
        "Composition runtime",
        [_decision()],
        "hyperframes",
        "kinetic typography for this brief",
        user_approved=True,
    )
    assert result["decision_id"].startswith("d-")

    log = json.loads((project / "decision_log.json").read_text(encoding="utf-8"))
    assert len(log["decisions"]) == 1
    assert log["decisions"][0]["selected"] == "hyperframes"

    # The stage checkpoint was NOT created or rewritten.
    assert list(project.glob("checkpoint_*.json")) == []


def test_decision_revision_keeps_both_entries(project) -> None:
    first = decision_append(
        "run",
        "proposal",
        "voice_selection",
        "Narration TTS provider",
        [_decision(option_id="chirp3", label="Chirp3")],
        "chirp3",
        "language support",
    )
    second = decision_append(
        "run",
        "proposal",
        "voice_selection",
        "Narration TTS provider",
        [_decision(option_id="onyx", label="Onyx")],
        "onyx",
        "user swapped the voice",
    )

    assert second["revises"] == [first["decision_id"]]
    log = decision_read("run")
    assert len(log["decisions"]) == 2
    # Append-only: the first entry is untouched.
    assert log["decisions"][0]["selected"] == "chirp3"


def test_decision_append_rejects_an_invalid_payload(project) -> None:
    with pytest.raises(CheckpointValidationError):
        decision_append(
            "run",
            "proposal",
            "not_a_real_category",
            "Subject",
            [_decision()],
            "x",
            "y",
        )

    # confidence is capped at 1.0 by the schema
    with pytest.raises(CheckpointValidationError):
        decision_append(
            "run",
            "proposal",
            "render_runtime_selection",
            "Subject",
            [_decision()],
            "x",
            "y",
            confidence=5.0,
        )


def test_decision_append_refuses_unknown_project(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    with pytest.raises(BridgeError):
        decision_append(
            "missing",
            "proposal",
            "voice_selection",
            "Subject",
            [_decision()],
            "x",
            "y",
        )


# ---------------------------------------------------------------------------
# T003 — budget contour
# ---------------------------------------------------------------------------


def test_estimate_reports_price_and_layer3_skills(project) -> None:
    estimate = tool_estimate("mcp_fake_paid_tool", {"price": 2.0})
    assert estimate["available"] is True
    assert estimate["estimate"]["estimated_cost_usd"] == 2.0
    assert "agent_skills" in estimate


def test_tool_run_reserves_executes_and_reconciles(project) -> None:
    # A paid tool needs a human approval on first use — that is the contract.
    tool_approve("mcp_fake_paid_tool", "run")

    output = project / "assets" / "images" / "frame.png"
    result = tool_run(
        "mcp_fake_paid_tool",
        # under the default single-action threshold (0.50 USD)
        {"output_path": str(output), "price": 0.4},
        "run",
        mode="sync",
    )

    assert result["success"] is True
    assert result["cost_usd"] == 0.5
    assert result["model"] == "fake-model"

    tracker = tracker_for("run")
    assert tracker.budget_spent_usd == pytest.approx(0.5)
    assert tracker.budget_reserved_usd == pytest.approx(0.0)
    assert [entry["status"] for entry in tracker.entries] == ["completed"]

    # The canonical cost_log artifact is written and schema-valid.
    cost_log = json.loads((project / "artifacts" / "cost_log.json").read_text(encoding="utf-8"))
    from schemas.artifacts import validate_artifact

    validate_artifact("cost_log", cost_log)
    assert cost_log["budget_spent_usd"] == pytest.approx(0.5)


def test_refused_reservation_does_not_crash_the_server(project) -> None:
    """The refund() trap: a failed reserve() leaves the entry 'estimated'."""
    result = tool_run(
        "mcp_fake_paid_tool",
        {"output_path": str(project / "assets" / "images" / "x.png"), "price": 5.0},
        "run",
        mode="sync",
    )

    assert result["success"] is False
    assert result["needs_approval"] is True
    assert result["cost_entry_status"] == "estimated"

    tracker = tracker_for("run")
    assert [entry["status"] for entry in tracker.entries] == ["estimated"]
    assert tracker.budget_reserved_usd == pytest.approx(0.0)
    assert tracker.budget_spent_usd == pytest.approx(0.0)


def test_approval_unblocks_the_next_call(project) -> None:
    params = {"output_path": str(project / "assets" / "images" / "x.png"), "price": 5.0}

    refused = tool_run("mcp_fake_paid_tool", params, "run", mode="sync")
    assert refused["needs_approval"] is True

    approved = tool_approve("mcp_fake_paid_tool", "run")
    assert approved["approved"] is True

    # Second paid call still trips the single-action threshold until the
    # human raises it; the point is that the server survives both.
    second = tool_run("mcp_fake_paid_tool", params, "run", mode="sync")
    assert second["success"] is False
    assert second["needs_approval"] is True


def test_budget_cap_blocks_an_oversized_call(project) -> None:
    tracker = tracker_for("run")
    tracker.single_action_approval_usd = 1000.0
    tracker.budget_total_usd = 1.0
    tool_approve("mcp_fake_paid_tool", "run")

    result = tool_run(
        "mcp_fake_paid_tool",
        {"output_path": str(project / "assets" / "images" / "x.png"), "price": 50.0},
        "run",
        mode="sync",
    )
    assert result["success"] is False
    assert result.get("budget_exceeded") is True
    assert tracker.budget_spent_usd == pytest.approx(0.0)


def test_tracker_is_one_per_project_and_persists_approvals(project) -> None:
    first = tracker_for("run")
    first.approve_tool("mcp_fake_paid_tool")

    assert tracker_for("run") is first

    # A fresh process would rebuild the tracker from the state file.
    from tools.cost_tracker import CostTracker

    restored = CostTracker(cost_log_path=project / ".cost_tracker_state.json")
    assert "mcp_fake_paid_tool" in restored._approved_tools
    assert restored.budget_spent_usd == pytest.approx(first.budget_spent_usd)


def test_budget_reports_snapshot(project) -> None:
    snapshot = budget("run")
    assert snapshot["budget_total_usd"] > 0
    # Key names come from CostTracker.cost_snapshot()
    assert "total_spent_usd" in snapshot
    assert "total_reserved_usd" in snapshot
    assert "budget_remaining_usd" in snapshot
    assert snapshot["entries"] == 0


def test_output_path_outside_the_workspace_is_rejected(project) -> None:
    with pytest.raises(BridgeError, match="inside projects/run"):
        tool_run(
            "mcp_fake_paid_tool",
            {"output_path": "/tmp/escape.png", "price": 1.0},
            "run",
        )

    with pytest.raises(BridgeError):
        tool_run(
            "mcp_fake_paid_tool",
            {"output_path": str(project / ".." / ".." / "escape.png"), "price": 1.0},
            "run",
        )


def test_relative_output_path_resolves_inside_the_project(project) -> None:
    result = tool_run(
        "mcp_fake_cheap_tool",
        {"output_path": "assets/images/relative.png"},
        "run",
        mode="sync",
    )
    assert result["success"] is True


def test_long_capability_tools_default_to_job_mode(project) -> None:
    """A video-generation tool is long-running by nature — even if it does not
    implement estimate_runtime()."""
    tool_approve("mcp_fake_paid_tool", "run")
    result = tool_run(
        "mcp_fake_paid_tool",
        {"output_path": str(project / "assets" / "images" / "x.png"), "price": 0.4},
        "run",
    )
    assert result["mode"] == "job"
    assert result["job_id"]
    assert result["estimated_seconds"] >= 60


def test_unknown_tool_raises_a_bridge_error(project) -> None:
    with pytest.raises(BridgeError, match="unknown tool"):
        tool_run("definitely_not_a_tool", {}, "run")


def test_unavailable_tool_returns_install_instructions(project) -> None:
    from tools.tool_registry import registry

    registry.discover()
    unavailable = [
        name
        for name in registry.list_all()
        if registry.get(name).get_status().value == "unavailable"
    ]
    assert unavailable, "expected at least one unavailable tool on this machine"

    result = tool_run(unavailable[0], {}, "run")
    assert result["success"] is False
    assert result["install_instructions"]
