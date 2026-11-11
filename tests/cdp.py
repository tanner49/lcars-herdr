"""Tiny headless-Chrome DevTools helper shared by the screenshot and voice tests."""

import asyncio
import contextlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

import aiohttp

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Page:
    def __init__(self, ws):
        self.ws, self.n, self.exceptions, self.console = ws, 0, [], []

    async def cmd(self, method, **params):
        self.n += 1
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        while True:
            m = json.loads((await self.ws.receive()).data)
            if m.get("method") == "Runtime.exceptionThrown":
                self.exceptions.append(m["params"]["exceptionDetails"].get("exception", {}).get("description"))
            elif m.get("method") == "Runtime.consoleAPICalled":
                self.console.append(" ".join(str(a.get("value", a.get("description"))) for a in m["params"]["args"]))
            if m.get("id") == self.n:
                return m.get("result", {})

    async def eval(self, expr):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    async def idle(self, seconds):
        """Sleep while still draining events (exceptions, console)."""
        await self.cmd("Runtime.evaluate", expression=f"new Promise(r => setTimeout(r, {int(seconds * 1000)}))",
                       awaitPromise=True)


@contextlib.asynccontextmanager
async def chrome(width=1600, height=1000, extra_flags=()):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as prof:
        proc = subprocess.Popen([CHROME, "--headless=new", "--hide-scrollbars", "--remote-debugging-address=127.0.0.1",
                                 "--remote-debugging-port=0", f"--user-data-dir={prof}", f"--window-size={width},{height}",
                                 *extra_flags, "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = Path(prof) / "DevToolsActivePort"
            for _ in range(100):
                if port_file.exists() and port_file.read_text().strip():
                    break
                time.sleep(0.1)
            port = port_file.read_text().split()[0]
            async with aiohttp.ClientSession() as s:
                targets = await (await s.get(f"http://127.0.0.1:{port}/json")).json()
                target = next(t for t in targets if t["type"] == "page")
                ws = await s.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=1 << 26)
                page = Page(ws)
                await page.cmd("Runtime.enable")
                await page.cmd("Emulation.setDeviceMetricsOverride", width=width, height=height,
                               deviceScaleFactor=1, mobile=False)
                yield page
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            await asyncio.sleep(0)
