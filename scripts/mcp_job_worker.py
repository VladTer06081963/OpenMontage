#!/usr/bin/env python3
"""Detached worker for a background MCP job.

Spawned by `lib.mcp_bridge._spawn_job`. It runs in its own session, so it
survives the MCP client timing out — and even the MCP server going away.

The worker:
  1. reads the job record written by the bridge;
  2. runs the tool exactly once;
  3. reconciles the budget entry it was given (reserved -> completed/failed);
  4. writes the result back into the job record.

It never resubmits anything: the job record is the single source of truth.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.config_model import BudgetMode
from lib.paths import PROJECTS_DIR
from tools.cost_tracker import CostTracker
from tools.provider_jobs import save_job
from tools.tool_registry import registry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(project_id: str, job_id: str) -> Path:
    return PROJECTS_DIR / project_id / "artifacts" / "jobs" / f"{job_id}.json"


def _write(path: Path, payload: dict) -> None:
    save_job(path, payload)


def _tracker_for(project_id: str) -> CostTracker:
    """The same tracker the bridge uses — loaded from the shared state file."""
    return CostTracker(
        budget_total_usd=10.0,
        mode=BudgetMode.CAP,
        cost_log_path=PROJECTS_DIR / project_id / ".cost_tracker_state.json",
    )


def _write_cost_log(project_id: str, tracker: CostTracker) -> None:
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
    path = PROJECTS_DIR / project_id / "artifacts" / "cost_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)

    project_id = args.project
    job_id = args.job
    path = _job_path(project_id, job_id)
    if not path.exists():
        print(f"job record not found: {path}", file=sys.stderr)
        return 2

    job = json.loads(path.read_text(encoding="utf-8"))
    if job.get("status") != "running":
        print(f"job is {job.get('status')}, nothing to do", file=sys.stderr)
        return 0

    tool_name = job.get("tool")
    params = job.get("params") or {}
    entry_id = job.get("cost_entry_id")

    registry.discover()
    instance = registry.get(tool_name)
    if instance is None:
        job.update({"status": "failed", "error": f"unknown tool: {tool_name}", "finished_at": _now_iso()})
        _write(path, job)
        return 3

    tracker = _tracker_for(project_id)
    try:
        result = instance.execute(params)
        job.update(
            {
                "status": "completed" if result.success else "failed",
                "data": result.data,
                "artifacts": result.artifacts,
                "error": result.error,
                "cost_usd": result.cost_usd,
                "model": result.model,
                "seed": result.seed,
                "duration_seconds": result.duration_seconds,
                "finished_at": _now_iso(),
            }
        )
        tracker.reload()
        tracker.reconcile(entry_id, result.cost_usd or 0.0, success=result.success)
    except Exception as exc:  # the worker must always leave a final record
        job.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[-2000:],
                "finished_at": _now_iso(),
            }
        )
        try:
            tracker.reload()
            tracker.reconcile(entry_id, 0.0, success=False)
        except Exception:
            pass

    _write(path, job)
    try:
        _write_cost_log(project_id, tracker)
    except Exception as exc:
        print(f"cost_log write failed: {exc}", file=sys.stderr)
    return 0 if job.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
