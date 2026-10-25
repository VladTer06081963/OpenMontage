#!/usr/bin/env python3
"""MCP server for OpenMontage — stdio transport.

Transport only. Every capability lives in ``lib/mcp_bridge.py`` and every
governance rule stays in the repository (checkpoints, gates, decision log,
tool registry). This file only maps bridge functions onto MCP tools.

Phase 0 (read-only): the exposed surface cannot execute a generation tool or
write into a project.

    python scripts/openmontage_mcp.py                 # stdio (default)
    python scripts/openmontage_mcp.py --http          # streamable HTTP :8765

Register with an MCP client (pi / Gemini CLI / Claude Code / OpenCode / Cursor):

    "openmontage": {
      "command": "/Users/mac/openMontag/.venv/bin/python",
      "args": ["/Users/mac/openMontag/scripts/openmontage_mcp.py"],
      "cwd": "/Users/mac/openMontag",
      "env": { "PYTHONPATH": "/Users/mac/openMontag" }
    }
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover - mcp 1.x fallback
    from mcp.server.fastmcp import FastMCP as MCPServer

from lib.mcp_bridge import (
    BridgeError,
    artifact_read,
    artifact_write,
    asset_list,
    board_state,
    budget,
    checkpoint_read,
    checkpoint_write,
    context_pack,
    decision_append,
    decision_read,
    job_list,
    job_result,
    job_status,
    list_pipelines,
    media_path,
    pipeline_manifest,
    preflight,
    project_init,
    project_state,
    read_skill,
    staging_copy,
    staging_roots,
    tool_approve,
    tool_estimate,
    tool_info,
    tool_list,
    tool_run,
)

INSTRUCTIONS = """\
OpenMontage is an instruction-driven video production system. This server
exposes capability discovery, pipeline manifests, project state, checkpoints,
artifacts, the live Backlot board, the instruction files (skills) that define
the quality bar for each stage, and (phase 1) governed tool execution.

Workflow for an agent:
  1. om_preflight            -> what this machine can do (providers, runtimes)
  2. om_pipelines            -> choose a pipeline
  3. om_pipeline_manifest    -> stages, gates, review focus
  4. om_context_pack         -> summary of a stage (not the full skill text)
  5. om_read_skill           -> load the director skill when you need it
  6. om_project_state        -> where the project stands, what is next
  7. om_tool_estimate        -> price of a call, BEFORE running it
  8. om_tool_run             -> run a tool (budget-checked, workspace-scoped)
  9. om_checkpoint_write     -> close the stage (gates enforced in the repo)

Gates, budgets and the decision log are enforced inside the repository, not
here: a checkpoint write for a gated stage without human approval raises a
GATE VIOLATION, and that behaviour must not be worked around. A paid tool that
needs approval returns needs_approval=true — ask a human and call
om_approve_tool; do not retry blindly.
"""

server = MCPServer(
    name="openmontage",
    instructions=INSTRUCTIONS,
)


def _safe(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Turn bridge failures into structured errors instead of tracebacks.

    Bridges may return a dict or a list; both are wrapped so the agent always
    gets ``{"ok": true, ...}`` and can branch on one shape.
    """
    try:
        result = fn(*args, **kwargs)
    except BridgeError as exc:
        return {"ok": False, "error": str(exc), "error_type": "bridge"}
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"not found: {exc}", "error_type": "not_found"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "error_type": "internal"}
    if isinstance(result, dict):
        return {"ok": True, **result}
    return {"ok": True, "items": result}


@server.tool(
    name="om_preflight",
    description="Capability menu: composition runtimes, per-capability provider counts, "
    "setup offers, runtime warnings.",
)
def om_preflight() -> dict:
    """What this machine can do right now (providers, runtimes, gaps)."""
    return _safe(preflight)


@server.tool(
    name="om_pipelines",
    description="All pipeline manifests with stage order and gated stages.",
)
def om_pipelines() -> dict:
    """List available pipelines."""
    return _safe(list_pipelines)


@server.tool(
    name="om_pipeline_manifest",
    description="One pipeline manifest: stages, produces, required artifacts, "
    "tools_available, human_approval_default, review_focus, success_criteria.",
)
def om_pipeline_manifest(pipeline: str) -> dict:
    """Normalized manifest for a single pipeline."""
    return _safe(pipeline_manifest, pipeline)


@server.tool(
    name="om_project_state",
    description="Full state of one project: marker, completed stages, next stage, "
    "checkpoints, decisions, assets, renders, board URL.",
)
def om_project_state(project_id: str) -> dict:
    """Where a project stands."""
    return _safe(project_state, project_id)


@server.tool(
    name="om_checkpoint_read",
    description="Read one checkpoint (stage) or a summary of all of them.",
)
def om_checkpoint_read(project_id: str, stage: str | None = None) -> dict:
    """Checkpoint contents for a project."""
    return _safe(checkpoint_read, project_id, stage)


@server.tool(
    name="om_artifact_read",
    description="Read one canonical artifact (research_brief, script, scene_plan, ...) "
    "from projects/<id>/artifacts/.",
)
def om_artifact_read(project_id: str, artifact: str) -> dict:
    """One canonical artifact."""
    return _safe(artifact_read, project_id, artifact)


@server.tool(
    name="om_board_state",
    description="Live Backlot board state for a project (loopback only).",
)
def om_board_state(project_id: str) -> dict:
    """Live board state, if the Backlot server is running."""
    return _safe(board_state, project_id)


@server.tool(
    name="om_tool_list",
    description="Catalog of registered tools with provider, status, runtime and "
    "best_for. Filter by capability or status.",
)
def om_tool_list(capability: str | None = None, status: str | None = None) -> dict:
    """Which tools exist and which are usable right now."""
    return _safe(tool_list, capability, status)


@server.tool(
    name="om_tool_info",
    description="Full contract of one tool: input/output schema, cost hints, "
    "fallbacks, dependencies, install_instructions, agent_skills (Layer 3).",
)
def om_tool_info(tool: str) -> dict:
    """Everything about one tool."""
    return _safe(tool_info, tool)


@server.tool(
    name="om_read_skill",
    description="Read an instruction file (skills/, .agents/skills/, docs/, AGENT_GUIDE.md). "
    "Supports section=<heading substring> and max_chars slicing so the context "
    "window survives.",
)
def om_read_skill(path: str, section: str | None = None, max_chars: int = 12000) -> dict:
    """Instruction text, sliced."""
    return _safe(read_skill, path, section, max_chars)


@server.tool(
    name="om_context_pack",
    description="Structured SUMMARY of a stage: goal, success criteria, review focus, "
    "gate flag, available tools, skill index. Deliberately does NOT inline full "
    "skill texts — use om_read_skill for those.",
)
def om_context_pack(pipeline: str, stage: str | None = None) -> dict:
    """Stage summary without the wall of text."""
    return _safe(context_pack, pipeline, stage)


# ---- Phase 1: governed execution ---------------------------------------


@server.tool(
    name="om_tool_estimate",
    description="Cost and runtime estimate for a tool call. ALWAYS call before a paid "
    "run. Returns the estimate, provider, availability and the Layer-3 skills that "
    "must be read before using the tool.",
)
def om_tool_estimate(tool: str, params: dict) -> dict:
    """What a call would cost and need."""
    return _safe(tool_estimate, tool, params)


@server.tool(
    name="om_tool_run",
    description="Execute one registered tool under the project's budget. Output paths "
    "must live inside projects/<project_id>/. mode='auto' (default) runs a long call as "
    "a detached background job and returns a job_id; mode='sync' blocks; mode='job' "
    "always detaches. Returns needs_approval=true when a human must approve the spend, "
    "or budget_exceeded=true when the cap is hit.",
)
def om_tool_run(
    tool: str,
    params: dict,
    project_id: str,
    dry_run: bool = False,
    mode: str = "auto",
) -> dict:
    """Run a tool, budget-checked."""
    return _safe(tool_run, tool, params, project_id, dry_run, mode)


@server.tool(
    name="om_job_status",
    description="State of a background job: running | completed | failed. Poll instead "
    "of resubmitting — a running job is already paid for.",
)
def om_job_status(project_id: str, job_id: str) -> dict:
    """Poll a background job."""
    return _safe(job_status, project_id, job_id)


@server.tool(
    name="om_job_result",
    description="Result of a finished background job: data, artifacts, cost, model, seed.",
)
def om_job_result(project_id: str, job_id: str) -> dict:
    """Collect a finished job's result."""
    return _safe(job_result, project_id, job_id)


@server.tool(
    name="om_job_list",
    description="All background jobs of a project, newest first.",
)
def om_job_list(project_id: str) -> dict:
    """Discover jobs (useful after a reconnect)."""
    return _safe(job_list, project_id)


@server.tool(
    name="om_approve_tool",
    description="Approve a tool for paid operations after a human approved the spend. "
    "Clears the needs_approval flag raised by om_tool_run. Approval persists per project.",
)
def om_approve_tool(tool: str, project_id: str) -> dict:
    """Approve a paid tool for this project."""
    return _safe(tool_approve, tool, project_id)


@server.tool(
    name="om_budget",
    description="Spend, reservations and remaining budget for a project, plus pending "
    "cost entries awaiting approval.",
)
def om_budget(project_id: str) -> dict:
    """Budget state for a project."""
    return _safe(budget, project_id)


@server.tool(
    name="om_decision_append",
    description="Append a decision to the project decision log WITHOUT touching the "
    "stage checkpoint. To revise a choice, reuse the same (category, subject). "
    "The log is append-only.",
)
def om_decision_append(
    project_id: str,
    stage: str,
    category: str,
    subject: str,
    options_considered: list[dict],
    selected: str,
    reason: str,
    user_approved: bool = False,
    decision_id: str | None = None,
    confidence: float | None = None,
) -> dict:
    """Record a decision mid-stage."""
    return _safe(
        decision_append,
        project_id,
        stage,
        category,
        subject,
        options_considered,
        selected,
        reason,
        user_approved,
        decision_id,
        confidence,
    )


@server.tool(
    name="om_decision_read",
    description="Read the full decision log of a project.",
)
def om_decision_read(project_id: str) -> dict:
    """The project audit trail."""
    return _safe(decision_read, project_id)


@server.tool(
    name="om_checkpoint_write",
    description="Write a stage checkpoint. GATE ENFORCEMENT IS IN THE REPOSITORY: a "
    "gated stage cannot be written as completed without human_approved=true — doing "
    "so raises a GATE VIOLATION and must not be worked around.",
)
def om_checkpoint_write(
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict,
    human_approved: bool = False,
    pipeline_type: str | None = None,
    review: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    """Close a stage — with the gate intact."""
    return _safe(
        checkpoint_write,
        project_id,
        stage,
        status,
        artifacts,
        human_approved,
        pipeline_type,
        review,
        None,
        metadata,
    )


@server.tool(
    name="om_artifact_write",
    description="Write a canonical artifact (research_brief, script, scene_plan, ...) "
    "into projects/<project_id>/artifacts/, validated against its schema.",
)
def om_artifact_write(project_id: str, artifact: str, payload: dict) -> dict:
    """Persist a canonical artifact."""
    return _safe(artifact_write, project_id, artifact, payload)


@server.tool(
    name="om_project_init",
    description="Create the project workspace: artifacts/, assets/, renders/ and "
    "project.json — the marker the Backlot board reads.",
)
def om_project_init(
    project_id: str,
    title: str,
    pipeline_type: str,
    style_playbook: str | None = None,
) -> dict:
    """Create a project workspace."""
    return _safe(project_init, project_id, title, pipeline_type, style_playbook)


# ---- Phase 1: bridge to the studio (ComfyUI / Resolve / CapCut) ----------


@server.tool(
    name="om_asset_list",
    description="List every file in the project workspace grouped by asset kind "
    "(images/video/audio/music) with sizes.",
)
def om_asset_list(project_id: str, kind: str | None = None) -> dict:
    """What the project has produced so far."""
    return _safe(asset_list, project_id, kind)


@server.tool(
    name="om_media_path",
    description="Absolute path of a project file, to hand to another MCP server "
    "(comfy_upload_image, Resolve media_pool import). Refuses paths outside the project.",
)
def om_media_path(project_id: str, rel_path: str) -> dict:
    """Project-relative path -> absolute path for an external studio."""
    return _safe(media_path, project_id, rel_path)


@server.tool(
    name="om_staging_roots",
    description="Candidate staging roots with existence and writability. DaVinci Resolve "
    "on macOS only imports from volumes the user granted — stage the file there first.",
)
def om_staging_roots() -> dict:
    """Where files can be staged for Resolve."""
    return _safe(staging_roots)


@server.tool(
    name="om_staging_copy",
    description="Copy a project file into a staging root another app can read "
    "(layout: <target_root>/<project_name>/<relative path>). Needed because Resolve "
    "refuses to import from arbitrary folders.",
)
def om_staging_copy(
    project_id: str,
    rel_path: str,
    target_root: str | None = None,
    project_name: str | None = None,
    overwrite: bool = False,
) -> dict:
    """Stage a project file for an external NLE."""
    return _safe(staging_copy, project_id, rel_path, target_root, project_name, overwrite)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--http" in argv:
        import os

        host = os.environ.get("OPENMONTAGE_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("OPENMONTAGE_MCP_PORT", "8765"))
        server.run(transport="streamable-http", host=host, port=port)
        return 0

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
