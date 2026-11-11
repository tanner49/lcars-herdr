"""Inferred-cursor check in headless Chrome, on its OWN scratch zsh tab.

Types real keys into the mirror and compares the drawn cursor column with where zsh's cursor must be
(prompt length + buffer position): trailing spaces, backspace over spaces and letters, arrows, ctrl-e, ctrl-w.

    .venv/bin/python tests/cursor_ui.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402

KEYS = {"bs": ("Backspace", "Backspace", 8, 0), "left": ("ArrowLeft", "ArrowLeft", 37, 0),
        "ctrl+e": ("e", "KeyE", 69, 2), "ctrl+w": ("w", "KeyW", 87, 2), "ctrl+u": ("u", "KeyU", 85, 2)}

# (action, expected buffer position after it, i.e. chars between prompt end and the cursor)
STEPS = [
    (None, 0),               # empty prompt: cursor after "% "
    ("echo ab", 7),
    ("  ", 9),               # two trailing spaces move the cursor straight away
    ("c", 10),
    ("bs", 9),               # deleting a letter that sat after spaces
    ("bs", 8),               # backspace over typed spaces
    ("bs", 7),
    ("left", 6), ("left", 5), ("left", 4),
    ("ctrl+e", 7),
    (" ", 8),
    ("x y", 11),             # "echo ab x y"
    ("ctrl+w", 10),          # zsh ctrl-w deletes "y", leaves "echo ab x "
]


async def main():
    tab, pane = await scratch.open("lcars-cursor-ui")
    print(f"scratch tab {tab} pane {pane}")
    fails = 0
    try:
        await asyncio.sleep(1.5)
        async with chrome() as page:
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}")
            await page.idle(2.0)
            await page.eval("document.querySelector('.xterm-helper-textarea').focus()")
            row = (await server.herdr("pane.read", {"pane_id": pane, "source": "visible"}))["read"]["text"].rstrip("\n").split("\n")[-1]
            prompt = row.rindex("%") + 2  # the text read trims the trailing space of "% "
            for action, want in STEPS:
                if action in KEYS:
                    key, code, vk, mods = KEYS[action]
                    await page.cmd("Input.dispatchKeyEvent", type="keyDown", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=mods)
                    await page.cmd("Input.dispatchKeyEvent", type="keyUp", key=key, code=code, windowsVirtualKeyCode=vk, modifiers=mods)
                elif action:
                    await page.cmd("Input.insertText", text=action)
                instant = await page.eval("window.__cursor && window.__cursor[1]")  # before the echo can arrive
                await page.idle(0.35)
                y, x = await page.eval("window.__cursor")
                got = x - prompt
                ok = got == want
                fails += not ok
                print(f"  {str(action):8} want {want:2}  got {got:2}  (immediately {instant - prompt if instant is not None else '-'})  {'ok' if ok else 'WRONG'}")
            if page.exceptions:
                print("page exceptions:", page.exceptions); fails += 1
        await server.herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")
    print("RESULT:", "PASS" if not fails else f"FAIL ({fails})")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
