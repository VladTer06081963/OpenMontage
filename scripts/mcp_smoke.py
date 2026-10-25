#!/usr/bin/env python3
"""Smoke test for the OpenMontage MCP server.

Spawns the real server over stdio with a real MCP client and exercises the
read-only surface. Run it after changing the tool surface or the bridge:

    make mcp-smoke
    .venv/bin/python scripts/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER = REPO_ROOT / "scripts" / "openmontage_mcp.py"
PYTHON = REPO_ROOT / ".venv" / "bin" / "python"

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
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


def _payload(result) -> dict:
    return json.loads(result.content[0].text)


async def _run() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    params = StdioServerParameters(
        command=str(PYTHON),
        args=[str(SERVER)],
        cwd=str(REPO_ROOT),
        env=env,
    )

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"server: {init.server_info.name}")

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            check(
                "tool surface matches the read-only contract",
                names == EXPECTED_TOOLS,
                f"{len(names)} tools",
            )

            pre = _payload(await session.call_tool("om_preflight", {}))
            counts = pre.get("tool_counts", {})
            check("om_preflight returns counts", counts.get("total", 0) > 0, json.dumps(counts))
            check(
                "om_preflight reports runtimes",
                set(pre.get("composition_runtimes", {})) == {"ffmpeg", "remotion", "hyperframes"},
            )

            pipes = _payload(await session.call_tool("om_pipelines", {}))
            check("om_pipelines lists manifests", len(pipes.get("items", [])) > 0)

            tools_payload = _payload(await session.call_tool("om_tool_list", {"capability": "tts"}))
            check(
                "om_tool_list wraps list results",
                isinstance(tools_payload.get("items"), list),
            )

            manifest = _payload(
                await session.call_tool("om_pipeline_manifest", {"pipeline": "animated-explainer"})
            )
            stages = {s["name"]: s for s in manifest.get("stages", [])}
            check("om_pipeline_manifest exposes gate flags", stages.get("proposal", {}).get("human_approval_default") is True)

            pack = _payload(
                await session.call_tool(
                    "om_context_pack", {"pipeline": "animated-explainer", "stage": "proposal"}
                )
            )
            check(
                "om_context_pack is a summary",
                len(json.dumps(pack, ensure_ascii=False)) <= 4_000,
                f"{len(json.dumps(pack, ensure_ascii=False))} chars",
            )

            skill = _payload(
                await session.call_tool(
                    "om_read_skill",
                    {
                        "path": "skills/pipelines/explainer/proposal-director.md",
                        "max_chars": 400,
                    },
                )
            )
            check("om_read_skill slices", skill.get("truncated") is True and skill.get("returned_chars", 0) < 1_000)

            blocked = _payload(await session.call_tool("om_read_skill", {"path": ".env"}))
            check("om_read_skill refuses paths outside the roots", blocked.get("ok") is False)

            # ---- phase 1: governed execution ----
            estimate = _payload(
                await session.call_tool("om_tool_estimate", {"tool": "audio_probe", "params": {}})
            )
            check("om_tool_estimate returns a price", estimate.get("ok") is True and "estimate" in estimate)

            tmp_id = "mcp-smoke-tmp"
            created = _payload(
                await session.call_tool(
                    "om_project_init",
                    {"project_id": tmp_id, "title": "Smoke", "pipeline_type": "framework-smoke"},
                )
            )
            check("om_project_init creates a workspace", created.get("ok") is True)

            decision = _payload(
                await session.call_tool(
                    "om_decision_append",
                    {
                        "project_id": tmp_id,
                        "stage": "proposal",
                        "category": "render_runtime_selection",
                        "subject": "Composition runtime",
                        "options_considered": [
                            {"option_id": "remotion", "label": "Remotion", "score": 0.8, "reason": "stock scenes"}
                        ],
                        "selected": "remotion",
                        "reason": "smoke test",
                    },
                )
            )
            check("om_decision_append records a decision", decision.get("ok") is True)

            log = _payload(await session.call_tool("om_decision_read", {"project_id": tmp_id}))
            check("om_decision_read shows it", len(log.get("decisions", [])) == 1)

            snap = _payload(await session.call_tool("om_budget", {"project_id": tmp_id}))
            check("om_budget reports the snapshot", snap.get("ok") is True and "budget_remaining_usd" in snap)

            # ---- phase 1: background job ----
            job = _payload(
                await session.call_tool(
                    "om_tool_run",
                    {
                        "tool": "subtitle_gen",
                        "params": {
                            "segments": [{"text": "Hello", "start": 0.0, "end": 1.0}],
                            "output_path": str(REPO_ROOT / "projects" / tmp_id / "assets" / "subtitles.srt"),
                        },
                        "project_id": tmp_id,
                        "mode": "job",
                    },
                )
            )
            check("om_tool_run starts a job", job.get("ok") is True and job.get("job_id"), str(job)[:120])

            job_id = job.get("job_id")
            finished = {}
            for _ in range(150):
                state = _payload(
                    await session.call_tool("om_job_status", {"project_id": tmp_id, "job_id": job_id})
                )
                finished = state
                if state.get("status") != "running":
                    break
                import asyncio

                await asyncio.sleep(0.2)
            check("the detached worker finished the job", finished.get("status") == "completed", str(finished)[:120])

            result = _payload(
                await session.call_tool("om_job_result", {"project_id": tmp_id, "job_id": job_id})
            )
            check("om_job_result returns the artifacts", result.get("ok") is True and bool(result.get("artifacts")))

            listing = _payload(await session.call_tool("om_job_list", {"project_id": tmp_id}))
            check("om_job_list finds the job", len(listing.get("items", [])) == 1)

            # ---- phase 1: studio bridge ----
            assets = _payload(await session.call_tool("om_asset_list", {"project_id": tmp_id}))
            check("om_asset_list walks the workspace", assets.get("ok") is True)

            roots = _payload(await session.call_tool("om_staging_roots", {}))
            check("om_staging_roots reports candidates", len(roots.get("items", [])) >= 1)

            staged = _payload(
                await session.call_tool(
                    "om_staging_copy",
                    {
                        "project_id": tmp_id,
                        "rel_path": "assets/subtitles.srt",
                        "target_root": str(REPO_ROOT / "projects" / tmp_id / "staging"),
                        "project_name": "Smoke",
                    },
                )
            )
            check("om_staging_copy stages the file", staged.get("ok") is True and bool(staged.get("destination")))

            import shutil

            shutil.rmtree(REPO_ROOT / "projects" / tmp_id, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("MCP server OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
