"""Mouse in the mirror, in headless Chrome, on its OWN scratch tab.

  1. mapping: drag across row "17" of `seq 1 40` in a shell -> xterm selects exactly that row
  2. click-to-move on a shell prompt: click the "h" of `echo hello world`, type X -> "echo Xhello world"
  3. mouse reports (agent panes; forced here with &mouse=1 on a scratch app that asks for ?1000/?1006):
     a click and a drag (with motion reports) arrive at the exact cells under the pointer

    .venv/bin/python tests/mouse_ui.py
"""

import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402

GEOM = """(() => { const s = document.querySelector('.xterm-screen').getBoundingClientRect(), t = window.__term;
  return {left: s.left, top: s.top, cw: s.width / t.cols, ch: s.height / t.rows, fs: t.options.fontSize}; })()"""


async def mouse(page, kind, x, y, buttons=1):
    await page.cmd("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left" if kind != "mouseMoved" or buttons else "none",
                   buttons=buttons, clickCount=1)


async def click(page, x, y):
    await mouse(page, "mousePressed", x, y); await mouse(page, "mouseReleased", x, y, 0)


async def drag(page, x1, y1, x2, y2):
    await mouse(page, "mousePressed", x1, y1)
    for i in range(1, 6):
        await mouse(page, "mouseMoved", x1 + (x2 - x1) * i / 5, y1 + (y2 - y1) * i / 5)
    await mouse(page, "mouseReleased", x2, y2, 0)


async def enter(page):
    await page.cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
    await page.cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)


async def main():
    tab, pane = await scratch.open("lcars-mouse-ui")
    print(f"scratch tab {tab} pane {pane}")
    ok = True
    try:
        await asyncio.sleep(1.5)
        async with chrome() as page:
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}")
            await page.idle(2.0)
            g = await page.eval(GEOM)
            print(f"font {g['fs']}px, cell {g['cw']:.2f}x{g['ch']:.2f}px")
            cell = lambda c, r: (g["left"] + (c + 0.5) * g["cw"], g["top"] + (r + 0.5) * g["ch"])

            # 1. mapping
            await page.cmd("Input.insertText", text="clear; seq 1 40"); await enter(page); await page.idle(0.8)
            rows = await page.eval("[...document.querySelectorAll('.xterm-rows > div')].map(r => r.textContent.trimEnd())")
            r17 = rows.index("17")
            x1, y = cell(0, r17); x2, _ = cell(1, r17)
            await drag(page, x1, y, x2 + g["cw"] * 0.4, y); await page.idle(0.3)
            sel = await page.eval("window.__term.getSelection()")
            print(f"1. drag on row {r17} ('17') selected {sel!r}")
            ok &= sel.strip() == "17"

            # 2. click-to-move on the shell prompt line
            await page.cmd("Input.insertText", text="echo hello world"); await page.idle(0.6)
            rows = await page.eval("[...document.querySelectorAll('.xterm-rows > div')].map(r => r.textContent)")
            ry = max(i for i, r in enumerate(rows) if "echo hello world" in r)
            hx = rows[ry].index("hello")
            await click(page, *cell(hx, ry)); await page.idle(0.5)
            cur = await page.eval("window.__cursor")
            await page.cmd("Input.insertText", text="X"); await page.idle(0.5)
            line = [ln for ln in (await server.herdr("pane.read", {"pane_id": pane, "source": "visible"}))["read"]["text"].split("\n")
                    if "echo" in ln and "world" in ln][-1]
            print(f"2. click on col {hx}: cursor -> {cur}; after typing X: {line.split('% ')[-1]!r}")
            ok &= cur == [ry, hx] and line.endswith("echo Xhello world")
            await server.herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})

            # 3. mouse reports to an app that asked for them (what Claude Code panes get)
            await page.cmd("Input.insertText", text=f"clear; {sys.executable} -I tests/mouse_echo.py"); await enter(page)
            await page.idle(1.2)
            await page.cmd("Page.navigate", url="about:blank"); await page.idle(0.3)  # a hash-only change doesn't reload
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}&mouse=1")
            await page.idle(2.0)
            g = await page.eval(GEOM)
            await click(page, *cell(19, 9)); await page.idle(0.4)
            await drag(page, *cell(4, 4), *cell(14, 7)); await page.idle(0.6)
            seen = (await server.herdr("pane.read", {"pane_id": pane, "source": "visible"}))["read"]["text"]
            evs = re.findall(r"EV (press|release) b=(\d+) col=(\d+) row=(\d+)", seen)
            clicks = [e for e in evs if e[1] == "0"]
            motion = [e for e in evs if e[1] == "32"]  # drag motion with the button held (?1002)
            print("3. app received clicks:", clicks, "| drag motion reports:", len(motion))
            want = [("press", "0", "20", "10"), ("release", "0", "20", "10"), ("press", "0", "5", "5"), ("release", "0", "15", "8")]
            ok &= clicks[:4] == want and len(motion) >= 3
            await page.cmd("Input.insertText", text="q"); await page.idle(0.4)
            if page.exceptions:
                print("page exceptions:", page.exceptions); ok = False
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
