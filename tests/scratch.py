"""Scratch shells for tests, kept out of the way.

Each scratch shell is the root tab of its own throwaway herdr workspace labelled "lcars-tests" (created unfocused),
so test tabs never land in any of your real workspaces. close() removes the whole workspace. The LCARS
board hides "lcars-tests" workspaces (tests that need to click them load the page with &tests=1).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

TEST_WORKSPACE = "lcars-tests"
ROOT = Path(__file__).resolve().parents[1]
_ws_of = {}


async def open(label: str = "scratch") -> tuple[str, str]:  # noqa: A001 - mirrors close()
    w = await server.herdr("workspace.create", {"label": TEST_WORKSPACE, "focus": False, "cwd": str(ROOT)})
    tab, pane = w["tab"]["tab_id"], w["root_pane"]["pane_id"]
    _ws_of[tab] = w["workspace"]["workspace_id"]
    try:
        await server.herdr("tab.rename", {"tab_id": tab, "label": label})
    except server.HerdrError:
        pass
    return tab, pane


async def workspace_of(tab: str) -> str:
    return _ws_of[tab]


async def close(tab: str):
    ws = _ws_of.pop(tab, None)
    if ws:
        await server.herdr("workspace.close", {"workspace_id": ws})
