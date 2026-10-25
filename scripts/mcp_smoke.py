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

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s): {', '.join(failures)}")
        return 1
    print("MCP server OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run()))
