"""Keystroke-to-screen latency, broken down. Creates and closes its OWN scratch tab.

    .venv/bin/python tests/latency.py            # herdr floor + LCARS end to end (server must be running)
    .venv/bin/python tests/latency.py --no-lcars # herdr floor only

Stages measured:
  rtt_send      pane.send_text round trip (new socket connection per call)
  rtt_read      pane.read visible+ansi round trip (new connection per call)
  rtt_read_pc   same, over one persistent connection (if herdr keeps connections open)
  herdr_floor   send_text -> char visible in pane.read (tight polling): PTY + shell echo + herdr emulator
  lcars_e2e     WebSocket input -> frame containing the char arrives at the client
"""

import argparse
import asyncio
import json
import os
import re
import secrets
import statistics
import sys
import time
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402
import scratch  # noqa: E402

herdr = server.herdr
BASE = "http://127.0.0.1:7477"


def stats(name, xs):
    xs = sorted(xs)
    print(f"  {name:12} n={len(xs):3}  p50={statistics.median(xs):6.1f} ms  p90={xs[int(len(xs) * .9) - 1]:6.1f} ms"
          f"  max={xs[-1]:6.1f} ms")


async def persistent_reads(pane, n):
    r, w = await asyncio.open_unix_connection(server.SOCKET, limit=1 << 24)
    out = []
    try:
        for i in range(n):
            t = time.perf_counter()
            w.write((json.dumps({"id": f"p{i}", "method": "pane.read",
                                 "params": {"pane_id": pane, "source": "visible", "format": "ansi",
                                            "strip_ansi": False}}) + "\n").encode())
            await w.drain()
            line = await asyncio.wait_for(r.readline(), 2)
            if not line:
                return None  # server closes after one request
            out.append((time.perf_counter() - t) * 1000)
    except (asyncio.TimeoutError, ConnectionError):
        return None
    finally:
        w.close()
    return out


async def main(lcars: bool, n: int):
    tab, pane = await scratch.open("lcars-latency")
    print(f"scratch tab {tab} pane {pane}")
    try:
        await asyncio.sleep(1.5)
        rd = lambda: herdr("pane.read", {"pane_id": pane, "source": "visible", "format": "ansi", "strip_ansi": False})

        sends, reads, floor = [], [], []
        for i in range(n):
            t0 = time.perf_counter(); await herdr("pane.send_text", {"pane_id": pane, "text": "x"})
            sends.append((time.perf_counter() - t0) * 1000)
            t0 = time.perf_counter(); await rd(); reads.append((time.perf_counter() - t0) * 1000)
        await herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})
        pc = await persistent_reads(pane, n)

        for i in range(n):
            mark = secrets.choice("abcdefghjkmnpqrstuvwyz") + str(i % 10)
            before = (await rd())["read"]["text"]
            t0 = time.perf_counter()
            await herdr("pane.send_text", {"pane_id": pane, "text": mark})
            while True:
                txt = (await rd())["read"]["text"]
                if txt != before and mark in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", txt):
                    break
                if time.perf_counter() - t0 > 2:
                    break
            floor.append((time.perf_counter() - t0) * 1000)
            await herdr("pane.send_keys", {"pane_id": pane, "keys": ["ctrl+u"]})
            await asyncio.sleep(0.05)

        print("herdr:")
        stats("rtt_send", sends)
        stats("rtt_read", reads)
        if pc:
            stats("rtt_read_pc", pc)
        else:
            print("  rtt_read_pc  herdr closes the connection after one request (no persistent mode)")
        stats("herdr_floor", floor)

        if lcars:
            async with aiohttp.ClientSession() as s:
                html = await (await s.get(BASE + "/")).text()
                token = re.search(r'LCARS_TOKEN = "([^"]+)"', html).group(1)
                ws = await s.ws_connect(BASE + f"/ws?token={token}", headers={"Origin": BASE})
                await ws.send_json({"type": "watch", "pane_id": pane})
                e2e = []
                last_frame = ""
                for i in range(n):
                    mark = "q" + secrets.token_hex(2)
                    # drain pending frames
                    while True:
                        try:
                            m = json.loads((await asyncio.wait_for(ws.receive(), 0.15)).data)
                            if m.get("type") == "frame":
                                last_frame = m["text"]
                        except asyncio.TimeoutError:
                            break
                    t0 = time.perf_counter()
                    await ws.send_json({"type": "input", "pane_id": pane, "data": mark})
                    while time.perf_counter() - t0 < 3:
                        m = json.loads((await ws.receive()).data)
                        if m.get("type") == "frame" and m["pane_id"] == pane and mark in m["text"]:
                            break
                    e2e.append((time.perf_counter() - t0) * 1000)
                    await ws.send_json({"type": "keys", "pane_id": pane, "keys": ["ctrl+u"]})
                await ws.close()
            print("lcars:")
            stats("lcars_e2e", e2e)
    finally:
        await scratch.close(tab)
        print(f"closed {tab}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-lcars", action="store_true")
    ap.add_argument("-n", type=int, default=30)
    a = ap.parse_args()
    asyncio.run(main(not a.no_lcars, a.n))
