"""Screenshot the running console with headless Chrome over the DevTools protocol.

Waits for live WebSocket data before capturing (plain `chrome --screenshot` fires at load).

    .venv/bin/python tests/screenshot.py docs/screenshot-board.png
    .venv/bin/python tests/screenshot.py docs/screenshot-detail.png --pane w7:pN
    LCARS_DEMO=1 LCARS_PORT=7478 .venv/bin/python server.py &   # fictional session, for README screenshots
    .venv/bin/python tests/screenshot.py docs/board.png --port 7478 --dpr 2
"""

import argparse
import asyncio
import base64
from pathlib import Path

from cdp import chrome


async def shoot(out: Path, url: str, wait: float, width: int, height: int, dpr: float = 1):
    async with chrome(width, height) as page:
        await page.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=dpr, mobile=False)
        await page.cmd("Page.navigate", url=url)
        await page.idle(wait)
        shot = await page.cmd("Page.captureScreenshot", format="png")
        out.write_bytes(base64.b64decode(shot["data"]))
        print(f"wrote {out}" + (f"  (page exceptions: {page.exceptions})" if page.exceptions else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--pane", help="pane id to open in the detail view")
    ap.add_argument("--wait", type=float, default=4.0)
    ap.add_argument("--size", default="1600x1000")
    ap.add_argument("--port", type=int, default=7477, help="7478 for a demo server (LCARS_DEMO=1 LCARS_PORT=7478)")
    ap.add_argument("--dpr", type=float, default=1)
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))
    url = f"http://127.0.0.1:{a.port}/" + (f"#pane={a.pane}" if a.pane else "")
    asyncio.run(shoot(a.out, url, a.wait, w, h, a.dpr))


if __name__ == "__main__":
    main()
