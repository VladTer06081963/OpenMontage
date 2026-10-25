"""Contract tests for the MCP bridge (summary/tasks/001).

Phase 0 is read-only, so these tests lock down three properties:
  1. the exposed MCP surface cannot execute a tool or write into a project;
  2. every path that leaves the bridge stays inside its declared root;
  3. the governance that guards later phases (gated checkpoints) is intact.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

from lib.checkpoint import CheckpointValidationError, init_project, write_checkpoint
from lib.mcp_bridge import (
    BridgeError,
    artifact_read,
    board_state,
    checkpoint_read,
    context_pack,
    list_pipelines,
    pipeline_manifest,
    preflight,
    project_state,
    read_skill,
    tool_info,
    tool_list,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SERVER_PATH = REPO_ROOT / "scripts" / "openmontage_mcp.py"


# ---------------------------------------------------------------------------
# Surface: read-only
# ---------------------------------------------------------------------------


def _load_server_module():
    spec = importlib.util.spec_from_file_location("openmontage_mcp_server", SERVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mcp_surface_matches_the_governed_contract() -> None:
    """Phase 1 surface: read + governed write. Nothing unguarded."""
    module = _load_server_module()
    names = {tool.name for tool in asyncio.run(module.server.list_tools())}

    assert names == {
        # phase 0 — read-only
        "om_preflight",
        "om_pipelines",
        "om_pipeline_manifest",
        "om_project_state",
        "om_checkpoint_read",
        "om_artifact_read",
        "om_board_state",
        "om_tool_list",
        "om_tool_info",
        "om_read_skill",
        "om_context_pack",
        # phase 1 — governed execution
        "om_tool_estimate",
        "om_tool_run",
        "om_approve_tool",
        "om_budget",
        "om_decision_append",
        "om_decision_read",
        "om_checkpoint_write",
        "om_artifact_write",
        "om_project_init",
        # phase 1 — background jobs
        "om_job_status",
        "om_job_result",
        "om_job_list",
        # phase 1 — studio bridge
        "om_asset_list",
        "om_media_path",
        "om_staging_roots",
        "om_staging_copy",
    }


def test_write_entrypoints_delegate_to_repository_functions(monkeypatch) -> None:
    """The bridge must not reimplement governance — it must call the repo."""
    import lib.checkpoint as checkpoint_mod
    import lib.mcp_bridge as bridge

    seen = {}

    def fake_write_checkpoint(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return Path("/tmp/checkpoint_x.json")

    monkeypatch.setattr(checkpoint_mod, "write_checkpoint", fake_write_checkpoint)
    bridge.checkpoint_write("run", "proposal", "awaiting_human", {}, human_approved=False)
    assert seen["args"][1] == "run"
    assert seen["args"][2] == "proposal"
    assert seen["kwargs"]["human_approved"] is False


def test_bridge_module_has_no_execute_entrypoint() -> None:
    """Phase 1 runs tools through the registry, not raw calls."""
    import lib.mcp_bridge as bridge

    assert not hasattr(bridge, "execute")
    assert hasattr(bridge, "tool_run")


def test_mcp_wrappers_normalize_dict_and_list_results() -> None:
    """A list-returning bridge must not be flattened into a broken dict."""
    module = _load_server_module()

    listing = module.om_pipelines()
    assert listing["ok"] is True
    assert isinstance(listing["items"], list)
    assert listing["items"]

    detail = module.om_preflight()
    assert detail["ok"] is True
    assert detail["tool_counts"]["total"] > 0

    failure = module.om_read_skill(".env")
    assert failure["ok"] is False
    assert failure["error_type"] == "bridge"


def test_gate_still_blocks_unapproved_completion(tmp_path) -> None:
    """The bridge does not write, but the gate it will have to respect is live."""
    init_project(
        "run",
        title="Run",
        pipeline_type="animated-explainer",
        pipeline_dir=tmp_path,
    )
    with pytest.raises(CheckpointValidationError):
        write_checkpoint(
            tmp_path,
            "run",
            "research",
            "completed",
            {},
            pipeline_type="animated-explainer",
            human_approval_required=False,
            human_approved=False,
        )


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../escape", "..", "a/b", "", "run;rm -rf", "x" * 200],
)
def test_project_id_traversal_is_rejected(bad_id: str) -> None:
    with pytest.raises(BridgeError):
        project_state(bad_id)


def test_artifact_read_rejects_traversal(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    project = tmp_path / "run"
    (project / "artifacts").mkdir(parents=True)
    (project / "artifacts" / "script.json").write_text('{"ok": true}', encoding="utf-8")

    assert artifact_read("run", "script") == {"ok": True}

    for attempt in ("../../etc/passwd", "../project.json", "/etc/passwd"):
        with pytest.raises(BridgeError):
            artifact_read("run", attempt)


def test_read_skill_rejects_paths_outside_allowed_roots() -> None:
    for attempt in (".env", "tools/base_tool.py", "../../../etc/passwd"):
        with pytest.raises(BridgeError):
            read_skill(attempt)


# ---------------------------------------------------------------------------
# Instructions: slicing, not dumping
# ---------------------------------------------------------------------------


def test_read_skill_respects_max_chars() -> None:
    result = read_skill("skills/pipelines/explainer/proposal-director.md", max_chars=500)
    assert result["truncated"] is True
    assert result["returned_chars"] <= 700  # 500 + truncation marker
    assert result["total_chars"] > 10_000


def test_read_skill_extracts_a_section() -> None:
    result = read_skill(
        "skills/pipelines/explainer/proposal-director.md",
        section="When to Use",
        max_chars=4000,
    )
    assert result["section"] == "When to Use"
    assert "When to Use" in result["content"]
    assert result["returned_chars"] < result["total_chars"]


def test_read_skill_unknown_section_is_an_error() -> None:
    with pytest.raises(BridgeError):
        read_skill("skills/pipelines/explainer/proposal-director.md", section="No Such Section")


def test_context_pack_is_a_summary_not_the_skill() -> None:
    pack = context_pack("animated-explainer", "proposal")
    encoded = json.dumps(pack, ensure_ascii=False)

    assert len(encoded) <= 4_000
    stage = pack["stages"][0]
    assert stage["stage"] == "proposal"
    assert stage["human_approval_default"] is True
    assert stage["skill_path"].endswith("proposal-director.md")
    # The skill body must NOT be inlined.
    assert stage["skill_size_chars"] > 1_000
    assert "success_criteria" not in encoded.split("skill_path")[0][:200]


def test_context_pack_rejects_unknown_stage() -> None:
    with pytest.raises(BridgeError):
        context_pack("animated-explainer", "not-a-stage")


# ---------------------------------------------------------------------------
# Capability + pipeline discovery
# ---------------------------------------------------------------------------


def test_preflight_reports_counts_and_runtimes() -> None:
    summary = preflight()
    counts = summary["tool_counts"]
    assert counts["total"] >= 1
    # Three buckets: available / unavailable / degraded. Degraded tools are
    # neither missing nor broken, so they must not be folded into unavailable.
    assert counts["available"] + counts["unavailable"] + counts["degraded"] == counts["total"]
    assert summary["status_counts"].get("available") == counts["available"]
    assert set(summary["composition_runtimes"]) == {"ffmpeg", "remotion", "hyperframes"}


def test_pipeline_manifest_exposes_gate_flags() -> None:
    manifest = pipeline_manifest("animated-explainer")
    stages = {stage["name"]: stage for stage in manifest["stages"]}
    assert stages["proposal"]["human_approval_default"] is True
    assert stages["research"]["human_approval_default"] is False
    assert stages["proposal"]["skill_path"].startswith("skills/")


def test_unknown_pipeline_raises_bridge_error() -> None:
    with pytest.raises(BridgeError):
        pipeline_manifest("no-such-pipeline")


def test_pipeline_list_is_non_empty_and_consistent() -> None:
    pipelines = list_pipelines()
    assert pipelines
    for entry in pipelines:
        assert entry["stages"]
        assert isinstance(entry["gated_stages"], list)


def test_tool_info_reports_availability_and_layer3_skills() -> None:
    info = tool_info("video_compose")
    assert info["name"] == "video_compose"
    assert "available" in info
    assert "input_schema" in info


def test_tool_list_filters_by_capability() -> None:
    video_tools = tool_list(capability="video_generation")
    assert all(tool["capability"] == "video_generation" for tool in video_tools)


def test_unknown_tool_raises_bridge_error() -> None:
    with pytest.raises(BridgeError):
        tool_info("no_such_tool")


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------


def test_project_state_for_unknown_project_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    with pytest.raises(BridgeError):
        project_state("missing")


def test_checkpoint_read_reports_missing_stage(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    (tmp_path / "run").mkdir()
    with pytest.raises(BridgeError):
        checkpoint_read("run", "research")


def test_board_state_degrades_when_backlot_is_down() -> None:
    state = board_state("how-ai-sees-the-world")
    assert "available" in state
    if not state["available"]:
        assert state["error"]
