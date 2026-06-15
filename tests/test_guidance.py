"""Tests for the v0.3.0 autonomy + guidance features.

Covers: server instructions, the maltego_guide tool, MCP prompts, and the
one-call maltego_investigate briefing (complete + no file writes).
"""

import asyncio
import os

import pytest

from maltego_mcp import guidance
from maltego_mcp.server import mcp, STORE


def call(name, args=None):
    async def _run():
        res = await mcp.call_tool(name, args or {})
        content = res[0] if isinstance(res, tuple) else res
        return content[0].text if isinstance(content, list) else str(content)

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _fresh_store():
    STORE._graphs.clear()
    STORE._active = None
    yield
    STORE._graphs.clear()
    STORE._active = None


def test_server_has_instructions():
    instr = mcp.instructions or ""
    assert len(instr) > 500
    # Mentions the autonomous workflow + the primary tool.
    assert "maltego_investigate" in instr
    assert "do not" in instr.lower() or "without" in instr.lower()


def test_guidance_module_matches_server():
    assert mcp.instructions == guidance.INSTRUCTIONS


def test_guide_tool_registered_and_returns_guidance():
    tools = asyncio.run(mcp.list_tools())
    assert any(t.name == "maltego_guide" for t in tools)
    out = call("maltego_guide")
    assert "maltego_investigate" in out and "Tool map" in out


def test_prompts_registered():
    prompts = asyncio.run(mcp.list_prompts())
    names = {p.name for p in prompts}
    assert {"investigate_prompt", "triage_prompt", "report_prompt"} <= names


def test_investigate_returns_complete_briefing():
    out = call(
        "maltego_investigate",
        {"params": {"query": "bob@example.com", "allow_network": False}},
    )
    # Detection + discoveries + next actions + inline report, all in one response.
    assert "Detected as **maltego.EmailAddress**" in out
    assert "Important discoveries" in out
    assert "Recommended next actions" in out
    assert "Investigation Report" in out  # inline report present by default


def test_investigate_can_omit_report_and_actions():
    out = call(
        "maltego_investigate",
        {
            "params": {
                "query": "example.com",
                "allow_network": False,
                "include_report": False,
                "include_next_actions": False,
            }
        },
    )
    assert "Investigation Report" not in out
    assert "Recommended next actions" not in out
    assert "Important discoveries" in out


def test_investigate_writes_no_files(tmp_path, monkeypatch):
    # Run inside an empty cwd and assert nothing was written.
    monkeypatch.chdir(tmp_path)
    call("maltego_investigate", {"params": {"query": "example.com", "allow_network": False}})
    assert os.listdir(tmp_path) == []


def test_investigate_domain_wrapper_returns_briefing():
    out = call(
        "maltego_investigate_domain",
        {"params": {"value": "example.com", "allow_network": False}},
    )
    assert "Investigation:" in out and "Important discoveries" in out
    assert "Investigation Report" in out


if __name__ == "__main__":
    import sys

    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in fns:
        try:
            if fn.__code__.co_argcount:
                continue  # skip fixture-dependent tests in standalone mode
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(fns) - failed} ran")
    sys.exit(1 if failed else 0)
