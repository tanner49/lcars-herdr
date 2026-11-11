"""End-to-end smoke test against a running server (./run.sh --no-open).

Creates its OWN scratch shell tab through the app, mirrors it, types into it, sends a synthesized
voice clip through whisper into it (no Enter), then closes that tab. Never touches other panes.

    .venv/bin/python tests/smoke.py
"""

import asyncio
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scratch  # noqa: E402

BASE = "http://127.0.0.1:7477"


async def main() -> int:
    async with aiohttp.ClientSession() as s:
        html = await (await s.get(BASE + "/")).text()
        token = re.search(r'LCARS_TOKEN = "([^"]+)"', html).group(1)

        # guard: no token / foreign origin are refused
        r = await s.post(BASE + "/api/transcribe", data=b"")
        assert r.status == 403, r.status
        r = await s.post(BASE + f"/api/transcribe?token={token}", data=b"", headers={"Origin": "https://evil.example"})
        assert r.status == 403, r.status
        print("guard: OK (403 without token, 403 foreign origin)")

        ws = await s.ws_connect(BASE + f"/ws?token={token}", headers={"Origin": BASE})
        board = json.loads((await ws.receive()).data)["board"]
        print(f"board: {len(board['tabs'])} tabs, herdr ok={board['herdr']['ok']}, voice={board['voice']}")
        for t in board["tabs"]:
            print(f"  {t['tab_id']:8} {t['status']:8} {t['label']:22} {(t['preview'] or [''])[-1][:70]!r}")

        holder, _ = await scratch.open("lcars-smoke-holder")  # throwaway workspace for the app-created tab
        ws_id = await scratch.workspace_of(holder)
        await ws.send_json({"type": "new_tab", "workspace_id": ws_id, "label": "lcars-smoke",
                            "cwd": str(Path(__file__).resolve().parents[1])})
        created = None
        while created is None:
            m = json.loads((await ws.receive()).data)
            if m["type"] == "created":
                created = m
            elif m["type"] == "toast":
                raise SystemExit(m["text"])
        tab, pane = created["tab_id"], created["pane_id"]
        print(f"created scratch tab {tab} pane {pane}")
        try:
            await asyncio.sleep(1.5)
            await ws.send_json({"type": "watch", "pane_id": pane})
            await ws.send_json({"type": "input", "pane_id": pane, "data": "echo LCARS-MIRROR-OK"})
            await ws.send_json({"type": "keys", "pane_id": pane, "keys": ["enter"]})

            async def wait_frame(pred, timeout=5.0):
                async def loop():
                    while True:
                        m = json.loads((await ws.receive()).data)
                        if m["type"] == "frame" and m["pane_id"] == pane and pred(m["text"]):
                            return m
                return await asyncio.wait_for(loop(), timeout)

            f = await wait_frame(lambda t: t.count("LCARS-MIRROR-OK") >= 2)
            print(f"mirror: OK ({f['cols']}x{f['rows']}, {len(f['text'])} bytes ANSI frame)")

            # voice: synthesize speech, 16 kHz wav, post with send=1 -> lands in the pane, no Enter
            with tempfile.TemporaryDirectory() as d:
                aiff, wav = Path(d) / "v.aiff", Path(d) / "v.wav"
                subprocess.run(["say", "-o", str(aiff), "Computer, list the open merge requests."], check=True)
                subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", "16000", "-ac", "1",
                                "-sample_fmt", "s16", str(wav)], check=True)
                body = wav.read_bytes()
            r = await s.post(BASE + f"/api/transcribe?pane_id={pane}&send=1", data=body,
                             headers={"X-LCARS-Token": token, "Content-Type": "audio/wav", "Origin": BASE})
            j = await r.json()
            print(f"voice: {j}")
            assert j.get("sent") and "merge requests" in j["text"].lower(), j
            f = await wait_frame(lambda t: "merge requests" in t.lower())
            last = [ln for ln in re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", f["text"]).split("\r\n") if ln.strip()][-1]
            print(f"voice landed at prompt, not submitted: {last.strip()!r}")
            await ws.send_json({"type": "keys", "pane_id": pane, "keys": ["ctrl+u"]})  # clear the line
        finally:
            await ws.send_json({"type": "close_tab", "tab_id": tab})
            await asyncio.sleep(1.0)
            await scratch.close(holder)
            print(f"closed scratch tab {tab} and its workspace")
        await ws.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
