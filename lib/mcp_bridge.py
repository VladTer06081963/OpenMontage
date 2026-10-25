"""Transport-free bridge between an external agent and an OpenMontage project.

Every function here is a plain Python call: unit-testable without an MCP
server, and reusable from a CLI or a future HTTP transport.
`scripts/openmontage_mcp.py` is the only place that maps these to MCP tools.

Phase 0 (read-only): nothing in this module executes a generation tool or
writes into a project. Every path that leaves the caller is resolved against
an explicit root and rejected if it escapes.

Design rules that keep the external agent honest:
- The registry is the source of truth for what is available — never a
  hardcoded provider list.
- The pipeline manifest is the source of truth for stages and gates.
- `context_pack` returns a *summary*; full skill texts are fetched on demand
  through `read_skill` so an agent's context window survives.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from lib.config_model import BudgetMode
from lib.paths import PROJECTS_DIR, REPO_ROOT
from tools.base_tool import ToolStatus
from tools.cost_tracker import (
    ApprovalRequiredError,
    BudgetExceededError,
    CostTracker,
)

# Roots an agent may read instructions from. Everything else is off-limits.
SKILLS_DIR = REPO_ROOT / "skills"
AGENT_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
DOCS_DIR = REPO_ROOT / "docs"
ALLOWED_READ_ROOTS = (SKILLS_DIR, AGENT_SKILLS_DIR, DOCS_DIR)

DEFAULT_SKILL_CHARS = 12_000
MAX_CONTEXT_PACK_CHARS = 4_000
MAX_SKILL_CHARS = 60_000

BACKLOT_PORT = int(os.environ.get("OPENMONTAGE_BACKLOT_PORT", "4750"))
PROJECT_BUDGET_USD = float(os.environ.get("OPENMONTAGE_MCP_BUDGET_USD", "10.0"))

# Staging roots: where files must sit before DaVinci Resolve will import
# them. Resolve on macOS runs in a sandbox and refuses to import from arbitrary
# folders — only from volumes the user has granted. The usual grant on this
# platform is ~/Movies/DaVinci Resolve.
STAGING_ROOT_ENV = "OPENMONTAGE_STAGING_ROOT"

# A call longer than this is run as a background job: the MCP client would
# otherwise time out while the provider keeps working — and a timed-out call
# that is retried is paid for twice.
JOB_THRESHOLD_SECONDS = float(os.environ.get("OPENMONTAGE_MCP_JOB_THRESHOLD_SECONDS", "60"))

# Capabilities whose tools are long-running by nature. Most tools do not
# implement estimate_runtime(), so the capability is the fallback signal.
LONG_CAPABILITIES = {
    "video_generation",
    "avatar",
    "3d_world_rendering",
    "3d_asset_generation",
    "3d_world_generation",
    "character_animation",
    "music_generation",
}

# One tracker per project for the lifetime of the server. The tracker persists
# its state to disk, so approvals and spend survive both MCP calls and a
# server restart. Creating a fresh tracker per call would silently reset the
# approved-tool set and the spend to zero.
_TRACKERS: dict[str, CostTracker] = {}

# Keys that name an output location a tool will write to.
_OUTPUT_PATH_KEYS = (
    "output_path",
    "output_dir",
    "output_file",
    "dest_path",
    "save_path",
    "render_path",
    "final_path",
)

_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


class BridgeError(RuntimeError):
    """A request the agent can fix (bad project id, missing file, bad path)."""


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _project_dir(project_id: str) -> Path:
    """Resolve a project directory, refusing anything that is not a plain id."""
    if not project_id or not _PROJECT_ID_RE.match(project_id):
        raise BridgeError(f"invalid project_id: {project_id!r}")
    return PROJECTS_DIR / project_id


def _resolve_in_project(project_id: str, rel_path: str) -> Path:
    """Resolve a path inside a project, refusing traversal outside it."""
    root = _project_dir(project_id).resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise BridgeError(f"path escapes project workspace: {rel_path!r}")
    return target


def _resolve_readable(rel_path: str) -> Path:
    """Resolve a readable file inside the allowed instruction roots."""
    raw = (REPO_ROOT / rel_path).resolve()
    for root in ALLOWED_READ_ROOTS:
        root = root.resolve()
        if raw == root or root in raw.parents:
            break
    else:
        # AGENT_GUIDE.md and a few top-level docs live outside the roots above.
        if raw.name != "AGENT_GUIDE.md" or raw.parent != REPO_ROOT.resolve():
            raise BridgeError(
                f"path is outside the readable roots: {rel_path!r} "
                f"(allowed: skills/, .agents/skills/, docs/, AGENT_GUIDE.md)"
            )
    if not raw.exists():
        raise BridgeError(f"file not found: {rel_path}")
    if not raw.is_file():
        raise BridgeError(f"not a file: {rel_path}")
    return raw


# ---------------------------------------------------------------------------
# Capability discovery
# ---------------------------------------------------------------------------


def preflight() -> dict[str, Any]:
    """Capability menu: what this machine can do right now.

    Note: tools have three statuses — available, unavailable and degraded.
    Degraded tools (e.g. ComfyUI adapters whose local server is down) are
    neither missing nor broken, so the breakdown is reported per status.
    Counts are environment-dependent: a status probe that needs the network
    reports differently when outbound sockets are blocked.
    """
    from collections import Counter

    from tools.tool_registry import registry

    registry.discover()
    summary = registry.provider_menu_summary()
    statuses = Counter(
        registry.get(name).get_status().value for name in registry.list_all()
    )
    return {
        "tool_counts": {
            "total": len(registry.list_all()),
            "available": statuses.get("available", 0),
            "unavailable": statuses.get("unavailable", 0),
            "degraded": statuses.get("degraded", 0),
        },
        "status_counts": dict(statuses),
        **summary,
    }


def tool_list(capability: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
    """Catalog of registered tools. Availability is dynamic — query, never assume."""
    from tools.tool_registry import registry

    registry.discover()
    tools = registry.get_by_capability(capability) if capability else registry.get_available()
    out = []
    for tool in tools:
        info_status = tool.get_status().value
        if status and info_status != status:
            continue
        out.append(
            {
                "name": tool.name,
                "capability": tool.capability,
                "provider": tool.provider,
                "status": info_status,
                "runtime": tool.runtime.value,
                "tier": tool.tier.value,
                "best_for": tool.best_for,
                "agent_skills": list(tool.agent_skills or []),
            }
        )
    return sorted(out, key=lambda item: item["name"])


def tool_info(tool: str) -> dict[str, Any]:
    """Full contract for one tool: schemas, cost hints, fallback, install steps."""
    from tools.tool_registry import registry

    registry.discover()
    instance = registry.get(tool)
    if instance is None:
        raise BridgeError(f"unknown tool: {tool}")
    info = dict(instance.get_info())
    info["available"] = info.get("status") == "available"
    return info


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def list_pipelines() -> list[dict[str, Any]]:
    """All pipeline manifests with their stage order and gated stages."""
    from lib.pipeline_loader import (
        get_stage_human_approval_default,
        get_stage_order,
        list_pipelines as _list,
        load_pipeline_readonly,
    )

    out = []
    for name in _list():
        manifest = load_pipeline_readonly(name)
        order = get_stage_order(manifest)
        out.append(
            {
                "pipeline": name,
                "name": manifest.get("name", name),
                "stability": manifest.get("stability"),
                "category": manifest.get("category"),
                "description": (manifest.get("description") or "").strip()[:300],
                "stages": order,
                "gated_stages": [
                    stage
                    for stage in order
                    if get_stage_human_approval_default(manifest, stage)
                ],
            }
        )
    return out


def pipeline_manifest(pipeline: str) -> dict[str, Any]:
    """One manifest, normalized for an agent: stages, gates, review focus."""
    from lib.pipeline_loader import (
        get_stage_human_approval_default,
        load_pipeline_readonly,
    )

    try:
        manifest = load_pipeline_readonly(pipeline)
    except FileNotFoundError as exc:
        raise BridgeError(f"unknown pipeline: {pipeline!r}") from exc

    stages = []
    for stage in manifest.get("stages", []):
        name = stage.get("name")
        skill = stage.get("skill")
        stages.append(
            {
                "name": name,
                "skill": skill,
                "skill_path": skill_path(skill) if skill else None,
                "produces": list(stage.get("produces", [])),
                "required_artifacts_in": list(stage.get("required_artifacts_in", [])),
                "tools_available": list(stage.get("tools_available", [])),
                "checkpoint_required": bool(stage.get("checkpoint_required", False)),
                "human_approval_default": get_stage_human_approval_default(manifest, name),
                "review_focus": list(stage.get("review_focus", [])),
                "success_criteria": list(stage.get("success_criteria", [])),
                "sub_stages": [sub.get("name") for sub in stage.get("sub_stages", [])],
            }
        )

    return {
        "pipeline": pipeline,
        "name": manifest.get("name", pipeline),
        "version": manifest.get("version"),
        "stability": manifest.get("stability"),
        "category": manifest.get("category"),
        "default_checkpoint_policy": manifest.get("default_checkpoint_policy"),
        "stages": stages,
    }


def skill_path(skill: str) -> Optional[str]:
    """Map a manifest skill reference (``pipelines/explainer/idea-director``)
    to its file on disk (``skills/pipelines/explainer/idea-director.md``)."""
    if not skill:
        return None
    candidates = [
        SKILLS_DIR / f"{skill}.md",
        SKILLS_DIR / skill / "SKILL.md",
        AGENT_SKILLS_DIR / f"{skill}.md",
        AGENT_SKILLS_DIR / skill / "SKILL.md",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate.relative_to(REPO_ROOT))
    return None


# ---------------------------------------------------------------------------
# Project state
# ---------------------------------------------------------------------------


def project_state(project_id: str) -> dict[str, Any]:
    """Everything an agent needs to orient itself in one project."""
    from lib.checkpoint import get_completed_stages, get_next_stage

    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")

    marker: dict[str, Any] = {}
    marker_path = project_dir / "project.json"
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            marker = {"_error": "project.json is not valid JSON"}

    pipeline_type = marker.get("pipeline_type") or marker.get("pipeline")
    checkpoints = _read_checkpoints(project_dir)
    completed: list[str] = []
    next_stage: Optional[str] = None
    if pipeline_type:
        try:
            completed = list(get_completed_stages(PROJECTS_DIR, project_id, pipeline_type))
            next_stage = get_next_stage(PROJECTS_DIR, project_id, pipeline_type)
        except Exception as exc:  # a broken manifest must not hide the project
            next_stage = f"<unavailable: {exc}>"

    return {
        "project_id": project_id,
        "project_dir": str(project_dir),
        "marker": marker,
        "pipeline_type": pipeline_type,
        "completed_stages": completed,
        "next_stage": next_stage,
        "checkpoints": checkpoints,
        "decisions": _decision_summary(project_dir),
        "assets": _asset_summary(project_dir),
        "renders": sorted(
            p.name for p in (project_dir / "renders").glob("*") if p.is_file()
        ) if (project_dir / "renders").exists() else [],
        "board_url": f"http://127.0.0.1:{BACKLOT_PORT}/p/{project_id}",
    }


def _read_checkpoints(project_dir: Path) -> list[dict[str, Any]]:
    out = []
    for path in sorted(project_dir.glob("checkpoint_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            out.append({"stage": path.stem.replace("checkpoint_", ""), "status": "unreadable"})
            continue
        out.append(
            {
                "stage": data.get("stage", path.stem.replace("checkpoint_", "")),
                "status": data.get("status"),
                "human_approved": data.get("human_approved", False),
                "human_approval_required": data.get("human_approval_required", False),
                "version": data.get("version"),
                "cost_snapshot": data.get("cost_snapshot"),
                "error": data.get("error"),
            }
        )
    return out


def _decision_summary(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "decision_log.json"
    if not path.exists():
        return {"count": 0, "recent": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"count": 0, "recent": [], "error": "decision_log.json is not valid JSON"}
    decisions = data.get("decisions", [])
    return {
        "count": len(decisions),
        "recent": [
            {
                "decision_id": item.get("decision_id"),
                "stage": item.get("stage"),
                "category": item.get("category"),
                "subject": item.get("subject"),
                "selected": item.get("selected"),
                "user_approved": item.get("user_approved", False),
            }
            for item in decisions[-10:]
        ],
    }


def _asset_summary(project_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    assets_dir = project_dir / "assets"
    if not assets_dir.exists():
        return summary
    for kind_dir in sorted(p for p in assets_dir.iterdir() if p.is_dir()):
        files = [p for p in kind_dir.iterdir() if p.is_file()]
        summary[kind_dir.name] = {"count": len(files), "files": [p.name for p in files[:50]]}
    return summary


def checkpoint_read(project_id: str, stage: Optional[str] = None) -> dict[str, Any]:
    """Read one checkpoint, or a summary of all of them."""
    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")

    if stage:
        path = project_dir / f"checkpoint_{stage}.json"
        if not path.exists():
            raise BridgeError(f"no checkpoint for stage {stage!r} in {project_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    return {
        "project_id": project_id,
        "checkpoints": _read_checkpoints(project_dir),
    }


def artifact_read(project_id: str, artifact: str) -> dict[str, Any]:
    """Read one canonical artifact from ``projects/<id>/artifacts/``."""
    if not _ARTIFACT_RE.match(artifact or ""):
        raise BridgeError(f"invalid artifact name: {artifact!r}")
    path = _resolve_in_project(project_id, f"artifacts/{artifact}.json")
    if not path.exists():
        raise BridgeError(f"artifact not found: {artifact} in {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_write(project_id: str, artifact: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Write a canonical artifact, validated against its schema."""
    from schemas.artifacts import validate_artifact

    if not _ARTIFACT_RE.match(artifact or ""):
        raise BridgeError(f"invalid artifact name: {artifact!r}")
    validate_artifact(artifact, payload)
    path = _resolve_in_project(project_id, f"artifacts/{artifact}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"project_id": project_id, "artifact": artifact, "path": str(path)}


def project_init(
    project_id: str,
    title: str,
    pipeline_type: str,
    style_playbook: Optional[str] = None,
) -> dict[str, Any]:
    """Create the project workspace: artifacts/, assets/, renders/, project.json."""
    from lib.checkpoint import init_project

    path = init_project(
        project_id,
        title=title,
        pipeline_type=pipeline_type,
        style_playbook=style_playbook,
    )
    return {"project_id": project_id, "project_dir": str(path), "pipeline_type": pipeline_type}


def checkpoint_write(
    project_id: str,
    stage: str,
    status: str,
    artifacts: dict[str, Any],
    human_approved: bool = False,
    pipeline_type: Optional[str] = None,
    review: Optional[dict[str, Any]] = None,
    cost_snapshot: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Write a stage checkpoint.

    Gate enforcement lives in `lib/checkpoint.py`: writing `completed` for a
    stage whose manifest sets `human_approval_default: true` without
    `human_approved=True` raises a GATE VIOLATION. That behaviour is not
    bypassable from here.
    """
    from lib.checkpoint import write_checkpoint

    path = write_checkpoint(
        PROJECTS_DIR,
        project_id,
        stage,
        status,
        artifacts,
        pipeline_type=pipeline_type,
        human_approved=human_approved,
        review=review,
        cost_snapshot=cost_snapshot,
        metadata=metadata,
    )
    return {"project_id": project_id, "stage": stage, "status": status, "path": str(path)}


def board_state(project_id: str) -> dict[str, Any]:
    """Live board state from Backlot (loopback only, best effort)."""
    url = f"http://127.0.0.1:{BACKLOT_PORT}/api/project/{project_id}/state"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"available": True, "url": url, "state": payload}
    except urllib.error.URLError as exc:
        return {
            "available": False,
            "url": url,
            "error": str(exc),
            "hint": "start the board with: python -m backlot open <project-id>",
        }
    except (TimeoutError, json.JSONDecodeError) as exc:
        return {"available": False, "url": url, "error": str(exc)}


# ---------------------------------------------------------------------------
# Execution + budget
# ---------------------------------------------------------------------------


def _get_tool(tool: str):
    from tools.tool_registry import registry

    registry.discover()
    instance = registry.get(tool)
    if instance is None:
        raise BridgeError(f"unknown tool: {tool}")
    return instance


def tracker_for(project_id: str) -> CostTracker:
    """The project's budget tracker — one instance, persisted to disk.

    `CostTracker._save()` writes entries, approved tools and the budget total;
    `__init__` restores them via `_load()`. The state file lives in the project
    root (not `artifacts/`, which holds canonical artifacts only).
    """
    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")
    if project_id not in _TRACKERS:
        _TRACKERS[project_id] = CostTracker(
            budget_total_usd=PROJECT_BUDGET_USD,
            mode=BudgetMode.CAP,
            cost_log_path=project_dir / ".cost_tracker_state.json",
        )
    return _TRACKERS[project_id]


def _assert_output_paths(project_id: str, params: dict[str, Any]) -> list[str]:
    """Every output location must resolve inside the project workspace.

    Inputs (image_path, script_path, ...) are not constrained: a tool may read
    a reference from anywhere. Writes are.
    """
    root = _project_dir(project_id).resolve()
    checked: list[str] = []
    for key, value in params.items():
        if not isinstance(value, str) or not value:
            continue
        if key not in _OUTPUT_PATH_KEYS and not (
            key.startswith("output") and key.endswith("_path")
        ):
            continue
        candidate = Path(value)
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if target != root and root not in target.parents:
            raise BridgeError(
                f"{key} must point inside projects/{project_id}/ (got {value!r})"
            )
        checked.append(key)
    return checked


def tool_estimate(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    """Cost and runtime estimate for a tool call — always before a paid run."""
    instance = _get_tool(tool)
    available = instance.get_status() is ToolStatus.AVAILABLE
    payload: dict[str, Any] = {
        "tool": instance.name,
        "provider": instance.provider,
        "capability": instance.capability,
        "available": available,
        "estimate": instance.dry_run(params),
        "agent_skills": list(instance.agent_skills or []),
    }
    if not available:
        payload["install_instructions"] = instance.install_instructions
    return payload


def tool_run(
    tool: str,
    params: dict[str, Any],
    project_id: str,
    dry_run: bool = False,
    mode: str = "auto",
) -> dict[str, Any]:
    """Execute one registered tool under the project's budget.

    Order of operations: availability -> workspace path check -> estimate ->
    reserve budget -> execute -> reconcile.

    On a refused reservation the cost entry stays in status `estimated`
    (nothing was reserved). `refund()` is deliberately NOT called there: it
    requires status `reserved` and would raise ValueError, killing the server.

    ``mode``:
      - ``auto`` (default): run as a background job when the estimated runtime
        is at or above ``JOB_THRESHOLD_SECONDS``;
      - ``job``: always run detached, return a ``job_id`` immediately;
      - ``sync``: always block (only safe for short tools).
    """
    instance = _get_tool(tool)
    if instance.get_status() is not ToolStatus.AVAILABLE:
        return {
            "success": False,
            "error": f"tool is {instance.get_status().value}",
            "install_instructions": instance.install_instructions,
        }

    _assert_output_paths(project_id, params)

    pre = instance.dry_run(params)
    estimate = pre.get("estimated_cost_usd") or 0.0
    runtime = _estimated_seconds(instance, params)
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "tool": instance.name,
            **pre,
            "estimated_seconds": runtime,
            "would_use_job": runtime >= JOB_THRESHOLD_SECONDS,
        }

    # Idempotency: an identical call that is already running or finished must
    # NOT be resubmitted — that would be a second charge.
    idem_key = f"{instance.name}:{instance.idempotency_key(params)}"
    existing = _find_job_by_key(project_id, idem_key)
    if existing is not None:
        return {
            "success": True,
            "job_id": existing["job_id"],
            "status": existing.get("status"),
            "idempotent_replay": True,
            "hint": "an identical call already exists — do not resubmit",
        }

    tracker = tracker_for(project_id)
    tracker.reload()
    entry_id = tracker.estimate(instance.name, "mcp_run", estimate)
    try:
        tracker.reserve(entry_id)
    except ApprovalRequiredError as exc:
        return {
            "success": False,
            "needs_approval": True,
            "error": str(exc),
            "estimated_cost_usd": estimate,
            "cost_entry_id": entry_id,
            "cost_entry_status": "estimated",
            "hint": "ask a human, then call om_approve_tool(tool, project_id) and retry",
        }
    except BudgetExceededError as exc:
        return {
            "success": False,
            "needs_approval": False,
            "budget_exceeded": True,
            "error": str(exc),
            "estimated_cost_usd": estimate,
            "cost_entry_id": entry_id,
            "cost_entry_status": "estimated",
        }

    if mode == "job" or (mode == "auto" and runtime >= JOB_THRESHOLD_SECONDS):
        job_id = _spawn_job(
            project_id,
            instance,
            params,
            entry_id,
            estimate,
            idem_key,
            runtime,
        )
        return {
            "success": True,
            "mode": "job",
            "job_id": job_id,
            "status": "running",
            "tool": instance.name,
            "provider": instance.provider,
            "estimated_cost_usd": estimate,
            "estimated_seconds": runtime,
            "cost_entry_id": entry_id,
            "hint": "poll om_job_status(job_id); do not resubmit",
        }

    result = instance.execute(params)
    tracker.reload()
    tracker.reconcile(entry_id, result.cost_usd or 0.0, success=result.success)
    _write_cost_log(project_id, tracker)
    return {
        "success": result.success,
        "tool": instance.name,
        "provider": instance.provider,
        "data": result.data,
        "artifacts": result.artifacts,
        "error": result.error,
        "cost_usd": result.cost_usd,
        "model": result.model,
        "seed": result.seed,
        "duration_seconds": result.duration_seconds,
        "cost_entry_id": entry_id,
    }


def _estimated_seconds(instance, params: dict[str, Any]) -> float:
    """Estimated wall time of a call, in seconds."""
    runtime = float(instance.estimate_runtime(params) or 0.0)
    if runtime <= 0 and instance.capability in LONG_CAPABILITIES:
        runtime = 120.0
    return runtime


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


def _job_dir(project_id: str) -> Path:
    return _project_dir(project_id) / "artifacts" / "jobs"


def _job_path(project_id: str, job_id: str) -> Path:
    return _job_dir(project_id) / f"{job_id}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_job(project_id: str, payload: dict[str, Any]) -> Path:
    """Persist a job record atomically (write .part, then rename)."""
    from tools.provider_jobs import save_job

    path = _job_path(project_id, payload["job_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    save_job(path, payload)
    return path


def _read_job(project_id: str, job_id: str) -> dict[str, Any]:
    path = _job_path(project_id, job_id)
    if not path.exists():
        raise BridgeError(f"job not found: {job_id} in {project_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"job record is corrupt: {job_id}") from exc


def _find_job_by_key(project_id: str, idem_key: str) -> Optional[dict[str, Any]]:
    """Find a running or finished job with the same idempotency key."""
    directory = _job_dir(project_id)
    if not directory.exists():
        return None
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("idempotency_key") != idem_key:
            continue
        if data.get("status") in {"running", "completed"}:
            return data
    return None


def _spawn_job(
    project_id: str,
    instance,
    params: dict[str, Any],
    entry_id: str,
    estimate: float,
    idem_key: str,
    runtime: float,
) -> str:
    """Start a detached worker and return its job id.

    The worker runs in its own session, so it survives the MCP client timing
    out and even the server going away.
    """
    job_id = f"job-{uuid.uuid4().hex[:10]}"
    payload = {
        "job_id": job_id,
        "project_id": project_id,
        "tool": instance.name,
        "provider": instance.provider,
        "status": "running",
        "params": params,
        "idempotency_key": idem_key,
        "cost_entry_id": entry_id,
        "estimated_cost_usd": estimate,
        "estimated_seconds": runtime,
        "started_at": _now_iso(),
    }
    _write_job(project_id, payload)

    log_path = _job_dir(project_id) / f"{job_id}.log"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    with open(log_path, "a", encoding="utf-8") as log:
        subprocess.Popen(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "mcp_job_worker.py"),
                "--project",
                project_id,
                "--job",
                job_id,
            ],
            cwd=str(REPO_ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )
    return job_id


def job_status(project_id: str, job_id: str) -> dict[str, Any]:
    """State of a background job: running | completed | failed."""
    job = _read_job(project_id, job_id)
    return {
        "job_id": job["job_id"],
        "project_id": project_id,
        "tool": job.get("tool"),
        "status": job.get("status"),
        "estimated_cost_usd": job.get("estimated_cost_usd"),
        "cost_usd": job.get("cost_usd"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "error": job.get("error"),
    }


def job_result(project_id: str, job_id: str) -> dict[str, Any]:
    """The finished job's result: data, artifacts, cost, model, seed."""
    job = _read_job(project_id, job_id)
    if job.get("status") == "running":
        return {
            "job_id": job["job_id"],
            "status": "running",
            "hint": "still running — poll om_job_status",
        }
    return {
        "job_id": job["job_id"],
        "status": job.get("status"),
        "tool": job.get("tool"),
        "provider": job.get("provider"),
        "data": job.get("data"),
        "artifacts": job.get("artifacts"),
        "error": job.get("error"),
        "cost_usd": job.get("cost_usd"),
        "model": job.get("model"),
        "seed": job.get("seed"),
        "duration_seconds": job.get("duration_seconds"),
    }


def job_list(project_id: str) -> list[dict[str, Any]]:
    """All background jobs of a project, newest first."""
    directory = _job_dir(project_id)
    if not directory.exists():
        return []
    jobs = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        jobs.append(
            {
                "job_id": data.get("job_id"),
                "tool": data.get("tool"),
                "status": data.get("status"),
                "estimated_cost_usd": data.get("estimated_cost_usd"),
                "cost_usd": data.get("cost_usd"),
                "started_at": data.get("started_at"),
                "finished_at": data.get("finished_at"),
            }
        )
    return sorted(jobs, key=lambda item: item.get("started_at") or "", reverse=True)


def tool_approve(tool: str, project_id: str) -> dict[str, Any]:
    """Approve a tool for paid operations (clears `ApprovalRequiredError`)."""
    tracker = tracker_for(project_id)
    tracker.approve_tool(tool)
    return {
        "tool": tool,
        "approved": True,
        "budget": tracker.cost_snapshot(),
    }


def budget(project_id: str) -> dict[str, Any]:
    """Spend, reservations and remaining budget for a project."""
    tracker = tracker_for(project_id)
    snapshot = tracker.cost_snapshot()
    return {
        "project_id": project_id,
        "budget_total_usd": tracker.budget_total_usd,
        **snapshot,
        "usable_budget_usd": round(tracker.usable_budget_usd, 4),
        "entries": len(tracker.entries),
        "pending_entries": [
            {"id": entry["id"], "tool": entry["tool"], "estimated_usd": entry.get("estimated_usd", 0.0)}
            for entry in tracker.entries
            if entry.get("status") == "estimated"
        ],
    }


def _write_cost_log(project_id: str, tracker: CostTracker) -> None:
    """Write the canonical `cost_log` artifact.

    The tracker's own state file is NOT a canonical artifact (it carries
    `approved_tools` and per-entry warning keys, and the artifact schema
    forbids extra properties). The artifact is a projection onto the schema.
    """
    from schemas.artifacts import validate_artifact

    payload = {
        "version": "1.0",
        "budget_total_usd": tracker.budget_total_usd,
        "budget_reserved_usd": round(tracker.budget_reserved_usd, 4),
        "budget_spent_usd": round(tracker.budget_spent_usd, 4),
        "entries": [
            {
                "id": entry["id"],
                "tool": entry["tool"],
                "operation": entry["operation"],
                "status": entry["status"],
                "estimated_usd": entry.get("estimated_usd", 0.0),
                "reserved_usd": entry.get("reserved_usd", 0.0),
                "actual_usd": entry.get("actual_usd", 0.0),
                "timestamp": entry["timestamp"],
            }
            for entry in tracker.entries
        ],
    }
    validate_artifact("cost_log", payload)
    path = _project_dir(project_id) / "artifacts" / "cost_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Decisions (append-only, stage-independent)
# ---------------------------------------------------------------------------


def decision_append(
    project_id: str,
    stage: str,
    category: str,
    subject: str,
    options_considered: list[dict[str, Any]],
    selected: str,
    reason: str,
    user_approved: bool = False,
    decision_id: Optional[str] = None,
    confidence: Optional[float] = None,
    user_visible: bool = True,
) -> dict[str, Any]:
    """Record a decision without touching the stage checkpoint.

    To revise an earlier choice, reuse the same (category, subject) — the log
    is append-only and the board renders the latest entry for that pair.
    """
    from lib.checkpoint import append_decision_log, read_decision_log

    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")

    decision: dict[str, Any] = {
        "decision_id": decision_id or f"d-{uuid.uuid4().hex[:8]}",
        "stage": stage,
        "category": category,
        "subject": subject,
        "options_considered": options_considered,
        "selected": selected,
        "reason": reason,
        "user_visible": user_visible,
        "user_approved": user_approved,
    }
    if confidence is not None:
        decision["confidence"] = confidence

    path = append_decision_log(PROJECTS_DIR, project_id, decision)

    existing = read_decision_log(PROJECTS_DIR, project_id).get("decisions", [])
    revises = [
        entry["decision_id"]
        for entry in existing
        if entry.get("category") == category
        and entry.get("subject") == subject
        and entry.get("decision_id") != decision["decision_id"]
    ]
    return {
        "project_id": project_id,
        "decision_id": decision["decision_id"],
        "path": str(path),
        "revises": revises,
        "decision_count": len(existing),
    }


def decision_read(project_id: str) -> dict[str, Any]:
    """The full decision log for a project."""
    from lib.checkpoint import read_decision_log

    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")
    return read_decision_log(PROJECTS_DIR, project_id)


def staging_roots() -> list[dict[str, Any]]:
    """Candidate staging roots, with whether they exist and are writable.

    DaVinci Resolve (macOS) only imports media from volumes the user granted.
    Ask the Resolve MCP for its own list (`media_storage.get_volumes()` in v2,
    `media_pool` capabilities in v3) and stage into the volume it reports.
    """
    candidates: list[Path] = []
    env_root = os.environ.get(STAGING_ROOT_ENV)
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(Path.home() / "Movies" / "DaVinci Resolve")

    roots = []
    for path in candidates:
        exists = path.is_dir()
        writable = exists and os.access(path, os.W_OK)
        roots.append(
            {
                "path": str(path),
                "exists": exists,
                "writable": writable,
                "source": "env" if env_root and str(path) == str(Path(env_root).expanduser()) else "default",
            }
        )
    return roots


def asset_list(project_id: str, kind: Optional[str] = None) -> dict[str, Any]:
    """Every file in the project workspace, grouped by asset kind."""
    project_dir = _project_dir(project_id)
    if not project_dir.exists():
        raise BridgeError(f"project not found: {project_id}")
    assets_dir = project_dir / "assets"
    if not assets_dir.is_dir():
        return {"project_id": project_id, "assets": {}, "total_bytes": 0, "total_files": 0}

    kinds = [kind] if kind else sorted(p.name for p in assets_dir.iterdir() if p.is_dir())
    grouped: dict[str, Any] = {}
    total_bytes = 0
    total_files = 0
    for name in kinds:
        directory = assets_dir / name
        if not directory.is_dir():
            continue
        files = []
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total_bytes += size
            total_files += 1
            files.append(
                {
                    "relative_path": str(path.relative_to(project_dir)),
                    "bytes": size,
                    "suffix": path.suffix.lower(),
                }
            )
        grouped[name] = {"count": len(files), "files": files}
    return {
        "project_id": project_id,
        "assets": grouped,
        "total_bytes": total_bytes,
        "total_files": total_files,
    }


def media_path(project_id: str, rel_path: str) -> dict[str, Any]:
    """Absolute path of a project file — to hand to another MCP server.

    ComfyUI (`comfy_upload_image`), Resolve (`media_pool` import) and the rest
    need a real filesystem path, not a project-relative one.
    """
    target = _resolve_in_project(project_id, rel_path)
    if not target.exists():
        raise BridgeError(f"file not found in project: {rel_path}")
    if not target.is_file():
        raise BridgeError(f"not a file: {rel_path}")
    return {
        "project_id": project_id,
        "relative_path": rel_path,
        "absolute_path": str(target),
        "bytes": target.stat().st_size,
        "suffix": target.suffix.lower(),
        "hint": "pass absolute_path to the target MCP server (comfy_upload_image, media_pool import)",
    }


def staging_copy(
    project_id: str,
    rel_path: str,
    target_root: Optional[str] = None,
    project_name: Optional[str] = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a project file into a staging root another app can read.

    DaVinci Resolve on macOS will not import from arbitrary folders, so media
    has to be copied into a granted volume first. The layout is deterministic:
    `<target_root>/<project_name>/<relative path>`.
    """
    source = _resolve_in_project(project_id, rel_path)
    if not source.exists():
        raise BridgeError(f"file not found in project: {rel_path}")
    if not source.is_file():
        raise BridgeError(f"not a file: {rel_path}")

    if target_root:
        root = Path(target_root).expanduser()
        # One level of auto-creation: the parent must exist, so a typo cannot
        # scatter a directory tree across the filesystem.
        if not root.is_dir():
            if not root.parent.is_dir():
                raise BridgeError(
                    f"staging root does not exist and its parent is missing: {root}"
                )
            root.mkdir(parents=True, exist_ok=True)
    else:
        candidates = [entry for entry in staging_roots() if entry["writable"]]
        if not candidates:
            raise BridgeError(
                "no writable staging root — set OPENMONTAGE_STAGING_ROOT or pass target_root"
            )
        root = Path(candidates[0]["path"])

    if not root.is_dir():
        raise BridgeError(f"staging root does not exist: {root}")

    destination = root / (project_name or project_id) / source.relative_to(
        _project_dir(project_id)
    )
    if destination.exists() and not overwrite:
        raise BridgeError(f"destination already exists (pass overwrite=true): {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "project_id": project_id,
        "source": str(source),
        "destination": str(destination),
        "bytes": destination.stat().st_size,
        "staging_root": str(root),
        "hint": "now import it through the Resolve MCP (media_pool import) — Resolve refuses paths outside granted volumes",
    }


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------


def read_skill(
    path: str,
    section: Optional[str] = None,
    max_chars: int = DEFAULT_SKILL_CHARS,
) -> dict[str, Any]:
    """Read an instruction file with slicing.

    ``section`` selects a markdown section by heading substring; ``max_chars``
    hard-caps the payload so an agent cannot blow its context window by
    accident.
    """
    limit = max(200, min(int(max_chars or DEFAULT_SKILL_CHARS), MAX_SKILL_CHARS))
    target = _resolve_readable(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    rel = str(target.relative_to(REPO_ROOT))
    outline = [f"{m.group(1)} {m.group(2).strip()}" for m in _HEADING_RE.finditer(text)]

    selected = text
    found_section = False
    if section:
        extracted = _extract_section(text, section)
        if extracted is None:
            raise BridgeError(f"section {section!r} not found in {rel}")
        selected = extracted
        found_section = True

    truncated = len(selected) > limit
    content = selected[:limit]
    if truncated:
        content += f"\n\n[truncated at {limit} chars — read the rest with om_read_skill]"

    return {
        "path": rel,
        "section": section if found_section else None,
        "total_chars": len(text),
        "returned_chars": len(content),
        "truncated": truncated,
        "outline": outline[:80],
        "content": content,
    }


def _extract_section(text: str, section: str) -> Optional[str]:
    """Slice a markdown section (heading + everything until the next heading
    of the same or higher level)."""
    needle = section.strip().lower()
    matches = list(_HEADING_RE.finditer(text))
    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        if needle not in heading.lower():
            continue
        level = len(match.group(1))
        start = match.start()
        end = len(text)
        for later in matches[index + 1 :]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        return text[start:end]
    return None


def context_pack(pipeline: str, stage: Optional[str] = None) -> dict[str, Any]:
    """Structured SUMMARY of a stage — never the full skill text.

    Keeps an external agent inside its context budget: the summary tells it
    *what* the stage demands, and `read_skill` delivers the instructions.
    """
    from lib.pipeline_loader import (
        get_stage_human_approval_default,
        load_pipeline_readonly,
    )

    try:
        manifest = load_pipeline_readonly(pipeline)
    except FileNotFoundError as exc:
        raise BridgeError(f"unknown pipeline: {pipeline!r}") from exc

    stages = manifest.get("stages", [])
    if stage:
        stages = [s for s in stages if s.get("name") == stage]
        if not stages:
            raise BridgeError(f"stage {stage!r} is not declared in pipeline {pipeline!r}")

    packs = []
    for entry in stages:
        name = entry.get("name")
        skill = entry.get("skill")
        resolved = skill_path(skill) if skill else None
        packs.append(
            {
                "stage": name,
                "goal": (entry.get("description") or entry.get("goal") or "").strip()[:400],
                "skill": skill,
                "skill_path": resolved,
                "skill_size_chars": _skill_size(resolved),
                "produces": list(entry.get("produces", [])),
                "required_artifacts_in": list(entry.get("required_artifacts_in", [])),
                "tools_available": list(entry.get("tools_available", [])),
                "checkpoint_required": bool(entry.get("checkpoint_required", False)),
                "human_approval_default": get_stage_human_approval_default(manifest, name),
                "success_criteria": list(entry.get("success_criteria", [])),
                "review_focus": list(entry.get("review_focus", [])),
            }
        )

    payload = {
        "pipeline": pipeline,
        "stage": stage,
        "stages": packs,
        "how_to_read_instructions": (
            "Call om_read_skill(path=..., section=...) to load the director skill "
            "before working this stage. Full texts are intentionally not inlined."
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > MAX_CONTEXT_PACK_CHARS:
        payload["_truncated"] = True
        payload["_note"] = (
            f"summary exceeded {MAX_CONTEXT_PACK_CHARS} chars; "
            "request a single stage or a specific section"
        )
        for item in payload["stages"]:
            item["success_criteria"] = item["success_criteria"][:6]
            item["review_focus"] = item["review_focus"][:6]
    return payload


def _skill_size(rel_path: Optional[str]) -> Optional[int]:
    if not rel_path:
        return None
    path = REPO_ROOT / rel_path
    try:
        return path.stat().st_size
    except OSError:
        return None
