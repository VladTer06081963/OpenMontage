"""Contract tests for the background-job mode (summary/tasks/004).

A long provider call must not die with the MCP client's timeout, and it must
never be paid for twice. The job runs in a detached worker process, so these
tests exercise the real thing: a real tool, a real subprocess, a real job
record on disk.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from lib.mcp_bridge import (
    BridgeError,
    job_list,
    job_result,
    job_status,
    tool_run,
)
from lib.checkpoint import init_project

POLL_SECONDS = 45.0


def _wait_for_job(project_id: str, job_id: str, timeout: float = POLL_SECONDS) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = job_status(project_id, job_id)
        if state["status"] != "running":
            return state
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A real project workspace, visible to the detached worker through env."""
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    monkeypatch.setenv("OPENMONTAGE_PROJECTS_DIR", str(tmp_path))
    init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )
    from lib.mcp_bridge import _TRACKERS

    _TRACKERS.clear()
    yield tmp_path / "run"
    _TRACKERS.clear()


def _segments() -> list[dict]:
    # subtitle_gen falls back to segment-level cues: text/start/end
    return [{"text": "Hello world", "start": 0.0, "end": 2.0}]


def test_job_mode_runs_detached_and_completes(project) -> None:
    output = project / "assets" / "subtitles.srt"
    started = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(output)},
        "run",
        mode="job",
    )

    assert started["success"] is True
    assert started["mode"] == "job"
    assert started["status"] == "running"
    job_id = started["job_id"]

    final = _wait_for_job("run", job_id)
    assert final["status"] == "completed", final.get("error")

    result = job_result("run", job_id)
    assert result["status"] == "completed"
    assert result["artifacts"]
    assert output.exists()


def test_identical_call_does_not_resubmit(project) -> None:
    """The paid-call rule: an identical call must not start a second job."""
    params = {
        "segments": _segments(),
        "output_path": str(project / "assets" / "subtitles.srt"),
    }

    first = tool_run("subtitle_gen", params, "run", mode="job")
    second = tool_run("subtitle_gen", params, "run", mode="job")

    assert second["job_id"] == first["job_id"]
    assert second["idempotent_replay"] is True

    _wait_for_job("run", first["job_id"])
    assert len(job_list("run")) == 1


def test_auto_mode_switches_to_a_job_when_the_threshold_is_met(project, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.JOB_THRESHOLD_SECONDS", 0.0)
    started = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(project / "assets" / "s.srt")},
        "run",
    )
    assert started["mode"] == "job"
    assert started["job_id"]
    _wait_for_job("run", started["job_id"])


def test_auto_mode_stays_sync_for_a_short_tool(project, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.JOB_THRESHOLD_SECONDS", 3600.0)
    result = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(project / "assets" / "s.srt")},
        "run",
    )
    assert result["success"] is True
    assert "job_id" not in result
    assert job_list("run") == []


def test_dry_run_reports_the_job_decision(project, monkeypatch) -> None:
    monkeypatch.setattr("lib.mcp_bridge.JOB_THRESHOLD_SECONDS", 0.0)
    preview = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(project / "assets" / "s.srt")},
        "run",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["would_use_job"] is True
    assert preview["estimated_seconds"] is not None
    # Nothing was executed and nothing was charged.
    assert job_list("run") == []


def test_budget_is_reserved_before_the_job_and_reconciled_after(project) -> None:
    from lib.mcp_bridge import tracker_for

    started = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(project / "assets" / "s.srt")},
        "run",
        mode="job",
    )
    tracker = tracker_for("run")
    tracker.reload()
    assert tracker.budget_reserved_usd == pytest.approx(0.0)  # free tool
    assert [entry["status"] for entry in tracker.entries] == ["reserved"]

    _wait_for_job("run", started["job_id"])

    tracker.reload()
    assert [entry["status"] for entry in tracker.entries] == ["completed"]
    assert tracker.budget_reserved_usd == pytest.approx(0.0)


def test_failed_job_records_the_error(project) -> None:
    started = tool_run(
        "subtitle_gen",
        {"segments": "not-a-list", "output_path": str(project / "assets" / "s.srt")},
        "run",
        mode="job",
    )
    final = _wait_for_job("run", started["job_id"])
    assert final["status"] == "failed"
    assert final["error"]


def test_job_status_of_an_unknown_job_raises(project) -> None:
    with pytest.raises(BridgeError, match="job not found"):
        job_status("run", "job-does-not-exist")


def test_job_record_lives_in_the_project(project) -> None:
    started = tool_run(
        "subtitle_gen",
        {"segments": _segments(), "output_path": str(project / "assets" / "s.srt")},
        "run",
        mode="job",
    )
    record = project / "artifacts" / "jobs" / f"{started['job_id']}.json"
    assert record.exists()
    payload = json.loads(record.read_text(encoding="utf-8"))
    assert payload["tool"] == "subtitle_gen"
    assert payload["idempotency_key"].startswith("subtitle_gen:")
