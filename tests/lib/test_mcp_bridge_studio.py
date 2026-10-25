"""Contract tests for the studio bridge (summary/tasks/006).

The project workspace and the external studio (ComfyUI, DaVinci Resolve) live in
different places: Resolve on macOS refuses to import media from arbitrary
folders. These tests lock down the hand-off.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.checkpoint import init_project
from lib.mcp_bridge import (
    BridgeError,
    asset_list,
    media_path,
    staging_copy,
    staging_roots,
)


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setattr("lib.mcp_bridge.PROJECTS_DIR", tmp_path)
    init_project(
        "run",
        title="Run",
        pipeline_type="framework-smoke",
        pipeline_dir=tmp_path,
    )
    workspace = tmp_path / "run"
    (workspace / "assets" / "video").mkdir(parents=True, exist_ok=True)
    (workspace / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    (workspace / "assets" / "video" / "clip.mp4").write_bytes(b"video-bytes")
    (workspace / "assets" / "audio" / "vo.mp3").write_bytes(b"audio-bytes")
    return workspace


def test_asset_list_groups_by_kind(project) -> None:
    listing = asset_list("run")
    assert listing["total_files"] == 2
    assert listing["assets"]["video"]["count"] == 1
    assert listing["assets"]["video"]["files"][0]["relative_path"] == "assets/video/clip.mp4"
    assert listing["assets"]["video"]["files"][0]["bytes"] == len(b"video-bytes")


def test_asset_list_can_filter_by_kind(project) -> None:
    listing = asset_list("run", kind="audio")
    assert set(listing["assets"]) == {"audio"}
    assert listing["total_files"] == 1


def test_media_path_resolves_inside_the_workspace(project) -> None:
    result = media_path("run", "assets/video/clip.mp4")
    assert result["absolute_path"] == str(project / "assets" / "video" / "clip.mp4")
    assert result["bytes"] == len(b"video-bytes")
    assert result["suffix"] == ".mp4"


def test_media_path_refuses_paths_outside_the_project(project) -> None:
    for attempt in ("../../etc/passwd", "/etc/passwd", ".."):
        with pytest.raises(BridgeError):
            media_path("run", attempt)


def test_media_path_reports_a_missing_file(project) -> None:
    with pytest.raises(BridgeError, match="file not found"):
        media_path("run", "assets/video/nope.mp4")


def test_staging_copy_creates_a_deterministic_layout(project, tmp_path) -> None:
    root = tmp_path / "resolve-volume"
    root.mkdir()

    result = staging_copy(
        "run",
        "assets/video/clip.mp4",
        target_root=str(root),
        project_name="My Project",
    )
    destination = root / "My Project" / "assets" / "video" / "clip.mp4"
    assert destination.exists()
    assert destination.read_bytes() == b"video-bytes"
    assert result["destination"] == str(destination)
    assert result["bytes"] == len(b"video-bytes")


def test_staging_copy_refuses_to_overwrite_silently(project, tmp_path) -> None:
    root = tmp_path / "resolve-volume"
    root.mkdir()
    staging_copy("run", "assets/video/clip.mp4", target_root=str(root))

    with pytest.raises(BridgeError, match="already exists"):
        staging_copy("run", "assets/video/clip.mp4", target_root=str(root))

    # explicit overwrite is allowed
    result = staging_copy(
        "run", "assets/video/clip.mp4", target_root=str(root), overwrite=True
    )
    assert Path(result["destination"]).exists()


def test_staging_copy_refuses_sources_outside_the_project(project, tmp_path) -> None:
    root = tmp_path / "resolve-volume"
    root.mkdir()
    with pytest.raises(BridgeError):
        staging_copy("run", "../../etc/passwd", target_root=str(root))


def test_staging_copy_creates_a_missing_root_one_level_deep(project, tmp_path) -> None:
    root = tmp_path / "resolve-volume"
    result = staging_copy(
        "run",
        "assets/video/clip.mp4",
        target_root=str(root),
        project_name="My Project",
    )
    assert root.is_dir()
    assert Path(result["destination"]).exists()


def test_staging_copy_refuses_a_root_whose_parent_is_missing(project, tmp_path) -> None:
    with pytest.raises(BridgeError, match="parent is missing"):
        staging_copy(
            "run",
            "assets/video/clip.mp4",
            target_root=str(tmp_path / "typo" / "deeper" / "volume"),
        )


def test_staging_roots_are_reported_with_their_state(monkeypatch, tmp_path) -> None:
    writable = tmp_path / "writable"
    writable.mkdir()
    monkeypatch.setenv("OPENMONTAGE_STAGING_ROOT", str(writable))

    roots = staging_roots()
    env_root = next(entry for entry in roots if entry["source"] == "env")
    assert env_root["path"] == str(writable)
    assert env_root["exists"] is True
    assert env_root["writable"] is True

    default_root = next(entry for entry in roots if entry["source"] == "default")
    assert "DaVinci Resolve" in default_root["path"]
