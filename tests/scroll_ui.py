"""Mouse-wheel scrolling and bracketed paste in the mirror, in headless Chrome, on its OWN scratch tab.

  1. shell scrollback: `seq 1 300`, wheel up -> herdr pane.scroll -> mirror shows earlier lines;
     typing a key snaps back to the live bottom
  2. full-screen app: `seq 1 1000 | less --mouse`, wheel down -> SGR wheel reports -> less scrolls
     (the path Claude Code panes use; agents themselves are never touched by tests)
  3. paste: a two-line paste arrives bracketed, so zsh holds it in the buffer instead of running line 1

    .venv/bin/python tests/scroll_ui.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402

TOP_ROW = "(() => { const r = document.querySelector('.xterm-rows > div'); return r ? r.textContent.trim() : ''; })()"


async def main():
    tab, pane = await scratch.open("lcars-scroll-ui")
    print(f"scratch tab {tab} pane {pane}")
    ok = True
    try:
        await asyncio.sleep(1.5)
        async with chrome() as page:
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}")
            await page.idle(2.0)
            focused = await page.eval("document.activeElement.className")
            print("focus on select:", focused)
            ok &= "xterm-helper-textarea" in focused

            async def type_text(txt):
                await page.cmd("Input.insertText", text=txt)

            async def enter():
                await page.cmd("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, text="\r")
                await page.cmd("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)

            box = await page.eval("(() => { const r = document.querySelector('.xterm-screen').getBoundingClientRect(); "
                                  "return [r.left + r.width / 2, r.top + r.height / 2]; })()")

            async def wheel(dy, n=1):
                for _ in range(n):
                    await page.cmd("Input.dispatchMouseEvent", type="mouseWheel", x=box[0], y=box[1], deltaX=0, deltaY=dy)
                    await page.idle(0.05)
                await page.idle(0.4)

            # 1. shell scrollback
            await type_text("clear; seq 1 300"); await enter(); await page.idle(0.8)
            bottom_top = await page.eval(TOP_ROW)
            await wheel(-100, 4)  # 4 steps of 4 = 16 steps -> 48 lines up
            up_top = await page.eval(TOP_ROW)
            off = (await server.herdr("pane.get", {"pane_id": pane}))["pane"]["scroll"]["offset_from_bottom"]
            print(f"shell: top row {bottom_top!r} -> after wheel up {up_top!r} | herdr offset {off}")
            ok &= up_top.isdigit() and bottom_top.isdigit() and int(up_top) < int(bottom_top) and off > 0
            await type_text("x"); await page.idle(0.5)
            snapped = (await server.herdr("pane.get", {"pane_id": pane}))["pane"]["scroll"]["offset_from_bottom"]
            print(f"shell: typing snapped back to bottom: offset {snapped}")
            ok &= snapped == 0
            await server.herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})

            # 2. full-screen app with mouse reporting
            await type_text("clear; seq 1 1000 | less --mouse"); await enter(); await page.idle(1.0)
            before = await page.eval(TOP_ROW)
            await wheel(100, 3)
            after = await page.eval(TOP_ROW)
            print(f"less --mouse: top row {before!r} -> after wheel down {after!r}")
            ok &= before == "1" and after.isdigit() and int(after) > 1
            await type_text("q"); await page.idle(0.4)

            # 3. bracketed paste
            await type_text("clear"); await enter(); await page.idle(0.4)
            await page.eval("""(() => { const dt = new DataTransfer(); dt.setData('text/plain', 'echo PASTE-ONE\\necho PASTE-TWO');
                document.querySelector('.xterm-helper-textarea').dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); })()""")
            await page.idle(0.6)
            seen = (await server.herdr("pane.read", {"pane_id": pane, "source": "visible"}))["read"]["text"]
            ran = [ln for ln in seen.split("\n") if ln.strip() == "PASTE-ONE"]
            print(f"paste: both lines in the buffer={('PASTE-ONE' in seen and 'PASTE-TWO' in seen)}, line 1 executed={bool(ran)}")
            ok &= "PASTE-TWO" in seen and not ran
            await server.herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+c"]})
            if page.exceptions:
                print("page exceptions:", page.exceptions); ok = False
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
