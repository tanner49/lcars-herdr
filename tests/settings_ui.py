"""SETTINGS panel and sounds, in headless Chrome, on its OWN scratch tab. Restores settings.json afterwards.

Text size (+ / AUTO), board list hide (AUTO text grows), talk key, mute / volume / click sounds,
agent alerts on synthetic status changes, and a custom sound file in sounds/ replacing a built-in tone.

    .venv/bin/python tests/settings_ui.py
"""

import asyncio
import io
import json
import math
import os
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import scratch  # noqa: E402
from cdp import chrome  # noqa: E402

SETTINGS = ROOT / "settings.json"
CUSTOM = ROOT / "sounds" / "done.wav"
FS = "window.__term.options.fontSize"


def tone_wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 880 * i / 16000))) for i in range(1600)))
    return buf.getvalue()


def restore_live(text):
    import re
    import urllib.request
    base = "http://127.0.0.1:7477"
    token = re.search(r'LCARS_TOKEN = "([^"]+)"', urllib.request.urlopen(base + "/").read().decode())[1]
    body = text if text is not None else json.dumps(server.SETTINGS_DEFAULTS)
    urllib.request.urlopen(urllib.request.Request(base + "/api/settings", data=body.encode(), method="POST", headers={
        "X-LCARS-Token": token, "Content-Type": "application/json", "Origin": base})).read()


async def main():
    saved = SETTINGS.read_text() if SETTINGS.exists() else None
    tab, pane = await scratch.open("lcars-settings-ui")
    print(f"scratch tab {tab} pane {pane}")
    checks = []
    check = lambda name, cond, info="": (checks.append((name, bool(cond))), print(f"  {'ok ' if cond else 'BAD'} {name} {info}"))
    try:
        await asyncio.sleep(1.5)
        async with chrome(extra_flags=["--autoplay-policy=no-user-gesture-required"]) as page:
            click = lambda sel: page.eval(f"document.querySelector({sel!r}).click()")
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}&tests=1")  # alerts need it on the board
            await page.idle(2.0)
            auto = await page.eval(FS)
            await click("#btn-settings"); await page.idle(0.3)
            check("settings opens", not await page.eval("document.getElementById('settings').hidden"))
            await click("#set-font-auto"); await page.idle(0.8)  # start from AUTO (your saved minimum is restored after)
            auto = await page.eval(FS)
            fill = await page.eval("window.__lcars.state.fitFont")
            check("AUTO: fills the width, at most 16", auto == min(16, fill) or abs(auto - fill) < 0.1, f"(auto {auto}, fill {fill})")
            await click("#set-font-up"); await page.idle(0.8)
            bigger = await page.eval(FS)
            sideways = await page.eval("(() => { const w = document.getElementById('term-wrap'); return w.scrollWidth > w.clientWidth; })()")
            check("text + : one point bigger (scrolls sideways if wider than the space)",
                  bigger == math.floor(auto) + 1 and sideways == (bigger > fill + 0.1), f"({auto} -> {bigger}, sideways {sideways})")
            await click("#set-font-dn"); await click("#set-font-dn"); await page.idle(0.8)
            smaller = await page.eval(FS)
            got = json.loads(SETTINGS.read_text())["font_size"]
            check("text - : goes below the fill size too (a fixed size)", smaller == math.floor(auto) - 1 == got, f"(-> {smaller}, saved {got})")
            await click("#set-font-auto"); await page.idle(0.6)
            check("AUTO restores the fit", await page.eval(FS) == auto)
            await click("[data-set=board_beside] [data-val=false]"); await page.idle(0.8)
            wide = await page.eval(FS)
            hidden = await page.eval("getComputedStyle(document.getElementById('board')).display")
            check("hide board list: board hidden, AUTO text bigger", hidden == "none" and wide > auto, f"({auto} -> {wide})")
            await click("[data-set=board_beside] [data-val=true]"); await page.idle(0.6)
            await click("[data-set=ptt_key] [data-val=MetaRight]"); await page.idle(0.3)
            check("talk key -> Right Command", "RIGHT COMMAND" in await page.eval("document.getElementById('vox-text').textContent"))
            await click("[data-set=ptt_key] [data-val=AltRight]"); await page.idle(0.3)

            # sounds: clicks, mute, volume
            clicks = lambda: page.eval("window.__sounds.filter(s => s.startsWith('click')).length")
            n0 = await clicks()
            await page.eval("document.getElementById('set-test').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            check("button click beeps", await clicks() == n0 + 1)
            await click("#set-mute"); await page.idle(0.3)
            n1 = await clicks()
            await page.eval("document.getElementById('set-done').dispatchEvent(new PointerEvent('pointerdown', {bubbles: true}))")
            check("muted: no click beep", await clicks() == n1)
            await click("#set-mute"); await page.idle(0.3)
            await page.eval("(() => { const v = document.getElementById('set-volume'); v.value = 30; v.dispatchEvent(new Event('change')); })()")
            await page.idle(0.5)
            disk = SETTINGS.read_text()
            check("volume + mute saved to settings.json", '"volume": 30' in disk and '"muted": false' in disk)
            await click("#set-done"); await page.idle(0.2)

            # agent alerts on status transitions (synthetic board, the scratch tab only)
            async def set_status(st):
                await page.eval(f"""(() => {{ const b = structuredClone(window.__lcars.state.board);
                    b.tabs.find(t => t.tab_id === {tab!r}).status = {st!r}; window.__lcars.handle({{type: 'board', board: b}}); }})()""")
            for st in ("working", "done"):
                await set_status(st)
            check("working -> done chirps", await page.eval("window.__sounds.includes('done')"))
            await page.idle(1.3)
            await set_status("blocked")
            check("-> blocked beeps", await page.eval("window.__sounds.includes('blocked')"))
            if page.exceptions:
                check("no page exceptions", False, page.exceptions)

        # custom sound file replaces the built-in tone
        CUSTOM.write_bytes(tone_wav())
        async with chrome() as page:
            await page.cmd("Page.navigate", url="http://127.0.0.1:7477/"); await page.idle(1.5)
            custom = await page.eval("window.LCARS_SOUNDS")
            await page.eval("document.getElementById('btn-settings').click()"); await page.idle(0.2)
            hint = await page.eval("document.getElementById('set-sound-hint').textContent")
            check("sounds/done.wav picked up", "/sounds/done.wav" in custom.get("done", []) and "done" in hint, f"({hint})")
    finally:
        CUSTOM.unlink(missing_ok=True)
        restore_live(saved)  # through the API, so open LCARS windows get the original settings back too
        if saved is None:
            SETTINGS.unlink(missing_ok=True)
        else:
            SETTINGS.write_text(saved)
        await scratch.close(tab)
        now = SETTINGS.read_text() if SETTINGS.exists() else None
        print(f"closed {tab}; settings.json restored: {now == saved}")
    bad = [n for n, ok in checks if not ok]
    print("RESULT:", "PASS" if not bad else f"FAIL {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
