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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from lib.paths import PROJECTS_DIR, REPO_ROOT

# Roots an agent may read instructions from. Everything else is off-limits.
SKILLS_DIR = REPO_ROOT / "skills"
AGENT_SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
DOCS_DIR = REPO_ROOT / "docs"
ALLOWED_READ_ROOTS = (SKILLS_DIR, AGENT_SKILLS_DIR, DOCS_DIR)

DEFAULT_SKILL_CHARS = 12_000
MAX_CONTEXT_PACK_CHARS = 4_000
MAX_SKILL_CHARS = 60_000

BACKLOT_PORT = int(os.environ.get("OPENMONTAGE_BACKLOT_PORT", "4750"))

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
