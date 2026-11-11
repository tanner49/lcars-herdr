"""Workspace rename from the console, in headless Chrome, on its OWN scratch workspace.

Creates workspace "lcars-ws-test" (unfocused), renames it through the RENAME pill + dialog, checks herdr
has the new label and the sidebar shows it, checks double-click + Esc cancels, then closes the workspace.

    .venv/bin/python tests/rename_ui.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
from cdp import chrome  # noqa: E402

NEW = "lcars ws renamed"


async def key(page, k, code, vk):
    await page.cmd("Input.dispatchKeyEvent", type="keyDown", key=k, code=code, windowsVirtualKeyCode=vk)
    await page.cmd("Input.dispatchKeyEvent", type="keyUp", key=k, code=code, windowsVirtualKeyCode=vk)


async def main():
    w = await server.herdr("workspace.create", {"label": "lcars-ws-test", "focus": False})
    ws_id = w["workspace"]["workspace_id"]
    print(f"scratch workspace {ws_id}")
    ok = True
    try:
        async with chrome() as page:
            await page.cmd("Page.navigate", url="http://127.0.0.1:7477/")
            await page.idle(2.5)
            sel = f'.ws-rename[data-rename="{ws_id}"]'
            found = await page.eval(f"!!document.querySelector('{sel}')")
            print("RENAME pill on the board:", found)
            ok &= found
            await page.eval(f"document.querySelector('{sel}').click()")
            await page.idle(0.3)
            dlg = await page.eval("({open: !document.getElementById('modal').hidden, value: document.getElementById('modal-input').value,"
                                  " title: document.getElementById('modal-title').textContent, focused: document.activeElement.id})")
            print("dialog:", dlg)
            ok &= dlg["open"] and dlg["value"] == "lcars-ws-test" and dlg["focused"] == "modal-input"
            await page.eval(f"(() => {{ const i = document.getElementById('modal-input'); i.value = {NEW!r}; }})()")
            await key(page, "Enter", "Enter", 13)
            await page.idle(1.0)
            label = (await server.herdr("workspace.get", {"workspace_id": ws_id}))["workspace"]["label"]
            side = await page.eval(f"document.querySelector('#ws-filters [data-filter=\"{ws_id}\"] span').textContent")
            print(f"herdr label now {label!r}; sidebar shows {side!r}")
            ok &= label == NEW and side == NEW
            # double-click the sidebar block, then Esc: no change
            await page.eval(f"document.querySelector('#ws-filters [data-filter=\"{ws_id}\"]').dispatchEvent(new MouseEvent('dblclick', {{bubbles: true}}))")
            await page.idle(0.3)
            opened = await page.eval("!document.getElementById('modal').hidden")
            await key(page, "Escape", "Escape", 27)
            await page.idle(0.5)
            label2 = (await server.herdr("workspace.get", {"workspace_id": ws_id}))["workspace"]["label"]
            closed = await page.eval("document.getElementById('modal').hidden")
            print(f"double-click opened dialog: {opened}; Esc closed it: {closed}; label unchanged: {label2 == NEW}")
            ok &= opened and closed and label2 == NEW
            if page.exceptions:
                print("page exceptions:", page.exceptions); ok = False
    finally:
        await server.herdr("workspace.close", {"workspace_id": ws_id})
        print(f"closed workspace {ws_id}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
