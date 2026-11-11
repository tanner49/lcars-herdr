"""Mirror fills its space, opens load, and click sounds are fixed per control. Headless Chrome at 2x (Retina),
on its OWN scratch tab; uses whatever settings.json says (e.g. a minimum text size).

    .venv/bin/python tests/layout_ui.py
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402

GAP = """(() => { const w = document.getElementById('term-wrap'), s = document.querySelector('.xterm-screen'), t = window.__term;
  if (!s || !t || document.getElementById('detail').hidden) return null; const wr = w.getBoundingClientRect(), sr = s.getBoundingClientRect();
  return {font: t.options.fontSize, lineH: t.options.lineHeight, size: t.cols + 'x' + t.rows, scrollsSideways: w.scrollWidth > w.clientWidth,
          right: Math.round(wr.right - sr.right), bottom: Math.round(wr.bottom - sr.bottom),
          rows: [...document.querySelectorAll('.xterm-rows > div')].filter(r => r.textContent.trim()).length}; })()"""


async def clickel(p, sel):
    r = await p.eval(f"(() => {{ const e = document.querySelector({sel!r}); e.scrollIntoView({{block:'center'}}); const b = e.getBoundingClientRect();"
                     f" return [b.left + Math.min(40, b.width / 2), b.top + Math.min(10, b.height / 2)]; }})()")
    for kind, buttons in (("mousePressed", 1), ("mouseReleased", 0)):
        await p.cmd("Input.dispatchMouseEvent", type=kind, x=r[0], y=r[1], button="left", buttons=buttons, clickCount=1)


async def main():
    tab, pane = await scratch.open("lcars-layout")
    print(f"scratch tab {tab} pane {pane}; settings font_size={server.load_settings()['font_size']}")
    bad = []
    ok = lambda name, cond, info="": (print(f"  {'ok ' if cond else 'BAD'} {name} {info}"), cond or bad.append(name))
    try:
        await asyncio.sleep(1.5)
        await server.herdr("pane.send_text", {"pane_id": pane, "text": "seq 1 30\r"})
        for W, H in ((1512, 945), (1728, 1117)):
            async with chrome(width=W, height=H, extra_flags=["--autoplay-policy=no-user-gesture-required"]) as p:
                await p.cmd("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=2, mobile=False)
                await p.cmd("Page.navigate", url="http://127.0.0.1:7477/#tests=1"); await p.idle(2.0)
                await clickel(p, f'.panel[data-tab="{tab}"]'); await p.idle(1.0)
                g = await p.eval(GAP)
                fills = g and g["rows"] >= 30 and (g["scrollsSideways"] or g["right"] <= 12) and g["bottom"] <= 12
                ok(f"{W}x{H} open from board: loads and fills", fills, g)
                await clickel(p, "#btn-unselect"); await p.idle(0.4)
                await clickel(p, f'.panel[data-tab="{tab}"]'); await p.idle(1.0)
                g = await p.eval(GAP); ok(f"{W}x{H} reopen same idle pane loads", g and g["rows"] >= 30, g)
                await clickel(p, f'.panel[data-tab="{tab}"]'); await p.idle(0.8)
                g = await p.eval(GAP); ok(f"{W}x{H} click selected panel: stays open, still fills", g and g["rows"] >= 30 and g["bottom"] <= 12, g)
                await p.eval("window.__lcars.state.settings.board_beside = false; document.getElementById('main').classList.add('board-hidden'); dispatchEvent(new Event('resize'))")
                await p.idle(1.0)
                g = await p.eval(GAP); ok(f"{W}x{H} board hidden: fills the wider space", g and (g["scrollsSideways"] or g["right"] <= 12) and g["bottom"] <= 12, g)
                await clickel(p, '[data-filter="all"]'); await p.idle(0.2)
                played = await p.eval("window.__sounds")
                ok(f"{W}x{H} fixed sounds per control", played[:5] == ["click-panel", "click-button", "click-panel", "click-panel", "click-menu"], played)
                if p.exceptions:
                    ok("no page exceptions", False, p.exceptions)
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")
    print("RESULT:", "PASS" if not bad else f"FAIL {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
