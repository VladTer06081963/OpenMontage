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

INSTRUCTIONS = """\
OpenMontage is an instruction-driven video production system. This server is
READ-ONLY (phase 0): it exposes capability discovery, pipeline manifests,
project state, checkpoints, artifacts, the live Backlot board, and the
instruction files (skills) that define the quality bar for each stage.

Workflow for an agent:
  1. om_preflight            -> what this machine can do (providers, runtimes)
  2. om_pipelines            -> choose a pipeline
  3. om_pipeline_manifest    -> stages, gates, review focus
  4. om_context_pack         -> summary of a stage (not the full skill text)
  5. om_read_skill           -> load the director skill when you need it
  6. om_project_state        -> where the project stands, what is next

Gates, budgets and the decision log are enforced inside the repository, not
here: a checkpoint write for a gated stage without human approval raises a
GATE VIOLATION, and that behaviour must not be worked around.
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
