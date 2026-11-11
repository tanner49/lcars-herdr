"""Browser keystroke -> painted echo latency in RAW KEYS mode, plus an inferred-cursor check.

Creates its OWN scratch shell tab, opens the console in headless Chrome, focuses the mirror,
types real key events at a given pace, and reads the page's own latency samples (window.__lat:
input sent -> changed frame for that input drawn + painted). Closes the tab afterwards.

    .venv/bin/python tests/latency_ui.py [--gap-ms 120] [-n 30] [--shot docs/screenshot-cursor.png]
"""

import argparse
import asyncio
import base64
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402


async def key(page, ch=None, code=None, vk=None, key_name=None, mods=0):
    p = {"modifiers": mods}
    if ch == " ":
        p.update(text=" ", key=" ", code="Space", windowsVirtualKeyCode=32)
    elif ch:
        p.update(text=ch, key=ch, code=f"Key{ch.upper()}" if ch.isalpha() else "", windowsVirtualKeyCode=ord(ch.upper()))
    else:
        p.update(key=key_name, code=code, windowsVirtualKeyCode=vk)
    await page.cmd("Input.dispatchKeyEvent", type="keyDown", **p)
    await page.cmd("Input.dispatchKeyEvent", type="keyUp", **{k: v for k, v in p.items() if k != "text"})


async def main(n, gap_ms, shot):
    tab, pane = await scratch.open("lcars-latency-ui")
    print(f"scratch tab {tab} pane {pane}")
    try:
        await asyncio.sleep(1.5)
        async with chrome() as page:
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}")
            await page.idle(2.0)
            await page.eval("document.querySelector('.xterm-helper-textarea').focus()")  # RAW KEYS mode
            await page.idle(0.3)
            word = "echo lcars latency check "
            for i in range(n):
                await key(page, word[i % len(word)])
                await page.idle(gap_ms / 1000)
            await page.idle(0.5)
            samples = await page.eval("window.__lat.slice()")
            # cursor check: move left 3, read the xterm cursor vs the end of the prompt row
            for _ in range(3):
                await key(page, code="ArrowLeft", vk=37, key_name="ArrowLeft")
            await page.idle(0.4)
            cursor = await page.eval("""(() => {
                const t = document.querySelector('.xterm-cursor, .xterm-cursor-block');
                const rows = [...document.querySelectorAll('.xterm-rows > div')];
                const y = rows.findIndex(r => r.querySelector('.xterm-cursor'));
                return { cursorRow: y, rowText: y >= 0 ? rows[y].textContent.trimEnd() : null, cell: window.__cursor,
                         endOfText: y >= 0 ? rows[y].textContent.trimEnd().length : null };
            })()""")
            if shot:
                Path(shot).write_bytes(base64.b64decode((await page.cmd("Page.captureScreenshot", format="png"))["data"]))
                print(f"wrote {shot}")
            if page.exceptions:
                print("page exceptions:", page.exceptions)
        if samples:
            s = sorted(samples)
            print(f"browser key->painted echo: n={len(s)} p50={statistics.median(s):.0f} ms "
                  f"p90={s[int(len(s) * .9) - 1]:.0f} ms max={s[-1]} ms  (typing gap {gap_ms} ms)")
        else:
            print("no latency samples recorded")
        print("cursor:", cursor)
        await server.herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--gap-ms", type=int, default=120)
    ap.add_argument("--shot")
    a = ap.parse_args()
    asyncio.run(main(a.n, a.gap_ms, a.shot))
