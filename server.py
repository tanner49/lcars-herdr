"""LCARS-style control surface for herdr, with local push-to-talk voice to text.

One process: an aiohttp server on 127.0.0.1 that
  * talks to the herdr server over its unix socket (newline-delimited JSON),
  * pushes a live agent board and pane frames to the browser over a WebSocket,
  * forwards keystrokes / text from the browser into a chosen pane,
  * transcribes push-to-talk audio with whisper.cpp (pywhispercpp, Metal GPU).

Run with ./run.sh (or .venv/bin/python server.py).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
import socket
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from aiohttp import WSMsgType, web

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
HOST = "127.0.0.1"  # never bind wider: this app can type into terminals
PORT = int(os.environ.get("LCARS_PORT", "7477"))
SOCKET = os.environ.get("HERDR_SOCKET_PATH", str(Path.home() / ".config/herdr/herdr.sock"))
MODEL_PATH = Path(os.environ.get("LCARS_WHISPER_MODEL", ROOT / "models/ggml-base.en.bin"))
DEMO = os.environ.get("LCARS_DEMO") == "1"  # fictional session from demo.py, no herdr needed
TOKEN = secrets.token_urlsafe(24)  # per-launch; embedded in index.html, required on every API call
SETTINGS_PATH = ROOT / "settings.json"  # UI settings (gitignored), shared by every window
SETTINGS_DEFAULTS = {"font_size": "auto", "board_beside": True, "ptt_key": "AltRight",
                     "muted": False, "volume": 60, "alert_sounds": True, "click_sounds": True}
SOUNDS_DIR = ROOT / "sounds"  # optional user sound files: <event>.wav|mp3|ogg|m4a override the built-in tones
SOUND_EVENTS = ("click", "click-menu", "click-panel", "click-button", "done", "blocked", "talk-start", "talk-stop", "error")
PTT_KEYS = {"AltRight", "MetaRight"}


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text())
    except (OSError, ValueError):
        saved = {}
    return clean_settings({**SETTINGS_DEFAULTS, **saved})


def clean_settings(s: dict) -> dict:
    out = dict(SETTINGS_DEFAULTS)
    fs = s.get("font_size")
    if fs == "auto" or (isinstance(fs, (int, float)) and not isinstance(fs, bool) and 7 <= fs <= 24):
        out["font_size"] = fs if fs == "auto" else round(float(fs) * 2) / 2
    for k in ("board_beside", "muted", "alert_sounds", "click_sounds"):
        if isinstance(s.get(k), bool):
            out[k] = s[k]
    v = s.get("volume")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        out["volume"] = int(max(0, min(100, v)))
    if s.get("ptt_key") in PTT_KEYS:
        out["ptt_key"] = s["ptt_key"]
    return out


def custom_sounds() -> dict:
    """{event: [urls]} for files in sounds/: <event>.<ext> or <event>-<anything>.<ext> (the page plays the first)."""
    found = {}
    if not SOUNDS_DIR.is_dir():
        return found
    files = sorted(p for p in SOUNDS_DIR.iterdir() if p.suffix.lower() in (".wav", ".mp3", ".ogg", ".m4a"))
    for ev in SOUND_EVENTS:
        urls = [f"/sounds/{p.name}" for p in files
                if p.stem == ev or (p.stem.startswith(ev + "-") and not any(
                    p.stem.startswith(o + "-") or p.stem == o for o in SOUND_EVENTS if o != ev and o.startswith(ev + "-")))]
        if urls:
            found[ev] = urls
    return found

BOARD_INTERVAL = 1.0    # snapshot poll (events also trigger an immediate refresh)
PREVIEW_INTERVAL = 2.0  # last-lines refresh for every pane
FRAME_IDLE = 0.05       # mirror poll for the watched pane (herdr has no output stream; a read costs ~0.2 ms)
FRAME_BURST = 0.008     # poll interval right after input, so echo shows up within a frame or two
BURST_SECONDS = 1.0
SHELLS = {"zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh", "csh", "nu", "elvish", "xonsh"}
PREVIEW_LINES = 6

log = logging.getLogger("lcars")


# --------------------------------------------------------------------------- herdr client

class HerdrError(RuntimeError):
    pass


class LineSocket:
    """Unix-socket line reader on loop.sock_* calls.

    asyncio's StreamReader.readline() took ~105 ms per response on the herdr socket (Python 3.15),
    while herdr itself answers in <1 ms and loop.sock_recv sees the bytes immediately.
    """

    def __init__(self, sock: socket.socket):
        self.sock, self.buf, self.loop = sock, b"", asyncio.get_running_loop()

    @classmethod
    async def connect(cls, path: str = SOCKET) -> "LineSocket":
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.setblocking(False)
        await asyncio.get_running_loop().sock_connect(s, path)
        return cls(s)

    async def send(self, obj: dict):
        await self.loop.sock_sendall(self.sock, (json.dumps(obj) + "\n").encode())

    async def readline(self) -> bytes:
        while b"\n" not in self.buf:
            chunk = await self.loop.sock_recv(self.sock, 1 << 20)
            if not chunk:
                line, self.buf = self.buf, b""
                return line
            self.buf += chunk
        line, _, self.buf = self.buf.partition(b"\n")
        return line + b"\n"

    def close(self):
        self.sock.close()


async def herdr(method: str, params: dict | None = None, timeout: float = 10.0) -> dict:
    """One request per connection (herdr closes after one response): write a JSON line, read one back."""
    conn = await asyncio.wait_for(LineSocket.connect(), timeout)
    try:
        await conn.send({"id": f"lcars-{secrets.token_hex(4)}", "method": method, "params": params or {}})
        line = await asyncio.wait_for(conn.readline(), timeout)
    finally:
        conn.close()
    msg = json.loads(line)
    if "error" in msg:
        raise HerdrError(f"{method}: {msg['error'].get('code')}: {msg['error'].get('message')}")
    return msg["result"]


# --------------------------------------------------------------------------- preview cleanup

_RULE_CHARS = set("─━═╭╮╰╯│┃▔▁-")
_CHROME = re.compile(r"(⏵⏵|auto mode on|shift\+tab to cycle|Update installed|esc to interrupt|\? for shortcuts|Compacted while idle|Skills restored)")


def _is_rule(s: str) -> bool:
    s = s.strip()
    return bool(s) and sum(c in _RULE_CHARS for c in s) > 0.6 * len(s)


def preview_lines(text: str, n: int = PREVIEW_LINES) -> list[str]:
    """Last n meaningful lines of a pane, minus agent TUI chrome (input box, mode line)."""
    lines = text.replace("\xa0", " ").split("\n")
    # Claude Code / Codex draw an input box at the bottom; drop everything from its top rule down.
    for i in range(len(lines) - 1, max(0, len(lines) - 12), -1):
        if lines[i].lstrip().startswith("❯") and _is_rule(lines[i - 1]):
            lines = lines[: i - 1]
            break
    out = [ln.rstrip() for ln in lines
           if ln.strip() and not _is_rule(ln) and not _CHROME.search(ln)
           and len(ln) - len(ln.lstrip()) < 40]  # right-aligned lines are TUI hints, not output
    return out[-n:]


# --------------------------------------------------------------------------- whisper

class Voice:
    def __init__(self, model_path: Path):
        self.model_path = model_path
        self.model = None
        self.error: str | None = None
        self.backend = "whisper.cpp"
        self.pool = ThreadPoolExecutor(max_workers=1)  # the model is not thread-safe

    def _load(self):
        from pywhispercpp.model import Model

        t = time.time()
        self.model = Model(str(self.model_path), n_threads=4, print_progress=False,
                           print_realtime=False, redirect_whispercpp_logs_to=None)
        self.model.transcribe(np.zeros(16000, dtype=np.float32))  # warm-up compiles Metal kernels
        log.info("whisper model %s ready in %.1fs", self.model_path.name, time.time() - t)

    async def load(self):
        if not self.model_path.exists():
            self.error = f"model missing: {self.model_path}"
            log.warning(self.error)
            return
        try:
            await asyncio.get_running_loop().run_in_executor(self.pool, self._load)
        except Exception as e:  # noqa: BLE001 - surface any load failure in the UI
            self.error = f"whisper load failed: {e}"
            log.exception("whisper load failed")

    def _run(self, audio: np.ndarray) -> str:
        segs = self.model.transcribe(audio, language="en", no_context=True)
        text = " ".join(s.text.strip() for s in segs)
        text = re.sub(r"\[(BLANK_AUDIO|MUSIC|NOISE)[^\]]*\]|\((silence|music|noise)[^)]*\)", "", text, flags=re.I)
        return re.sub(r"\s+", " ", text).strip()

    async def transcribe(self, audio: np.ndarray) -> str:
        if self.model is None:
            raise RuntimeError(self.error or "whisper model still loading")
        return await asyncio.get_running_loop().run_in_executor(self.pool, self._run, audio)

    def status(self) -> dict:
        if self.error:
            return {"state": "error", "detail": self.error}
        if self.model is None:
            return {"state": "loading", "detail": f"loading {self.model_path.name}"}
        return {"state": "ready", "detail": f"{self.backend} · {self.model_path.stem.replace('ggml-', '')} · Metal"}


def decode_audio(body: bytes, content_type: str) -> np.ndarray:
    if content_type in ("audio/wav", "audio/x-wav", "audio/wave"):
        with wave.open(io.BytesIO(body)) as w:
            if w.getframerate() != 16000 or w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise ValueError("wav must be 16 kHz mono 16-bit")
            return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    # default: raw little-endian float32 PCM, 16 kHz mono (what the browser sends)
    return np.frombuffer(body, dtype="<f4").astype(np.float32)


# --------------------------------------------------------------------------- app state

class Hub:
    def __init__(self, voice: Voice):
        self.voice = voice
        self.clients: dict[web.WebSocketResponse, dict] = {}
        self.snapshot: dict = {}
        self.previews: dict[str, list[str]] = {}
        self.board_json = ""
        self.refresh = asyncio.Event()
        self.created_tabs: set[str] = set()  # tabs this app created; only these may be closed from the UI
        self.herdr_ok = False
        self.herdr_error = ""

    # ---- board
    def build_board(self) -> dict:
        s = self.snapshot
        ws_label = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in s.get("workspaces", [])}
        ws_order = {w["workspace_id"]: w.get("number", 0) for w in s.get("workspaces", [])}
        agents = {a["pane_id"]: a for a in s.get("agents", [])}
        panes_by_tab: dict[str, list[dict]] = {}
        for p in s.get("panes", []):
            panes_by_tab.setdefault(p["tab_id"], []).append(p)
        rects = {}
        for lay in s.get("layouts", []):
            for p in lay.get("panes", []):
                rects[p["pane_id"]] = p["rect"]
        tabs = []
        for t in s.get("tabs", []):
            panes = panes_by_tab.get(t["tab_id"], [])
            # the pane to show: the agent pane if there is one, else the first
            main = next((p for p in panes if p["pane_id"] in agents), panes[0] if panes else None)
            agent = agents.get(main["pane_id"]) if main else None
            pid = main["pane_id"] if main else None
            tabs.append({
                "tab_id": t["tab_id"],
                "label": t.get("label") or t["tab_id"],
                "number": t.get("number"),
                "workspace_id": t["workspace_id"],
                "workspace_label": ws_label.get(t["workspace_id"], t["workspace_id"]),
                "workspace_number": ws_order.get(t["workspace_id"], 0),
                "status": (agent or {}).get("agent_status") or t.get("agent_status") or "unknown",
                "agent": None if not agent else {
                    "name": agent.get("name"),
                    "kind": agent.get("agent"),
                    "title": agent.get("terminal_title_stripped"),
                },
                "pane_id": pid,
                "pane_count": t.get("pane_count", len(panes)),
                "rect": rects.get(pid),
                "cwd": (main or {}).get("foreground_cwd") or (main or {}).get("cwd"),
                "focused": bool(t.get("focused")),
                "preview": self.previews.get(pid or "", []),
                "app_created": t["tab_id"] in self.created_tabs,
            })
        tabs.sort(key=lambda x: (x["workspace_number"], x["number"] or 0))
        return {
            "tabs": tabs,
            "workspaces": [{"workspace_id": w["workspace_id"], "label": ws_label[w["workspace_id"]]}
                           for w in sorted(s.get("workspaces", []), key=lambda w: w.get("number", 0))],
            "herdr": {"ok": self.herdr_ok, "error": self.herdr_error, "version": s.get("version")},
            "voice": self.voice.status(),
        }

    async def broadcast(self, msg: dict):
        data = json.dumps(msg)
        for ws in list(self.clients):
            try:
                await ws.send_str(data)
            except Exception:  # noqa: BLE001 - dead socket, dropped on next loop
                self.clients.pop(ws, None)

    async def push_board(self, force: bool = False):
        board = self.build_board()
        j = json.dumps(board, sort_keys=True)
        if force or j != self.board_json:
            self.board_json = j
            await self.broadcast({"type": "board", "board": board})

    # ---- loops
    async def snapshot_loop(self):
        while True:
            try:
                if DEMO:
                    import demo
                    self.snapshot = demo.snapshot()
                    self.herdr_ok, self.herdr_error = True, ""
                    await self.push_board()
                    await asyncio.sleep(BOARD_INTERVAL)
                    continue
                self.snapshot = (await herdr("session.snapshot"))["snapshot"]
                self.herdr_ok, self.herdr_error = True, ""
            except Exception as e:  # noqa: BLE001
                self.herdr_ok, self.herdr_error = False, str(e)
            await self.push_board()
            self.refresh.clear()
            try:
                await asyncio.wait_for(self.refresh.wait(), BOARD_INTERVAL)
            except asyncio.TimeoutError:
                pass

    async def preview_loop(self):
        while True:
            if DEMO:
                import demo
                self.previews = demo.previews()
                await self.push_board()
                await asyncio.sleep(PREVIEW_INTERVAL)
                continue
            panes = [p["pane_id"] for p in self.snapshot.get("panes", [])]

            async def one(pid):
                try:
                    r = await herdr("pane.read", {"pane_id": pid, "source": "visible"})
                    self.previews[pid] = preview_lines(r["read"]["text"])
                except Exception:  # noqa: BLE001 - pane may have closed mid-loop
                    self.previews.pop(pid, None)

            await asyncio.gather(*(one(p) for p in panes))
            for gone in set(self.previews) - set(panes):
                self.previews.pop(gone, None)
            await self.push_board()
            await asyncio.sleep(PREVIEW_INTERVAL)

    async def event_loop(self):
        """Subscribe to herdr events; any event triggers an immediate board refresh."""
        base = ["tab.created", "tab.closed", "tab.renamed", "tab.focused", "pane.created", "pane.closed",
                "pane.exited", "pane.agent_detected", "workspace.created", "workspace.closed", "workspace.renamed"]
        if DEMO:
            await asyncio.Event().wait()  # no herdr events in demo mode
        while True:
            pane_ids = sorted(p["pane_id"] for p in self.snapshot.get("panes", []))
            subs = [{"type": t} for t in base] + [
                {"type": "pane.agent_status_changed", "pane_id": p} for p in pane_ids]
            try:
                conn = await LineSocket.connect()
                await conn.send({"id": "lcars-sub", "method": "events.subscribe", "params": {"subscriptions": subs}})
                while True:
                    # re-subscribe when the pane set changes so status events cover new panes
                    now = sorted(p["pane_id"] for p in self.snapshot.get("panes", []))
                    if now != pane_ids:
                        break
                    try:
                        line = await asyncio.wait_for(conn.readline(), 2.0)
                    except asyncio.TimeoutError:
                        continue
                    if not line:
                        break
                    if b"subscription_started" not in line:
                        self.refresh.set()
                conn.close()
            except Exception:  # noqa: BLE001 - herdr restarting; retry
                await asyncio.sleep(2)

    async def wheel(self, ws: web.WebSocketResponse, st: dict, m: dict):
        """Mouse wheel over the mirror.

        herdr has no wheel API. A pane with host scrollback (a shell) scrolls via pane.scroll. An agent pane
        (Claude Code: alternate screen, asks for ?1000/?1006 mouse reports) gets SGR wheel reports, as herdr
        sends it. Other full-screen apps get arrow keys, which is safe for apps that never asked for the mouse.
        """
        pid, up, count = m["pane_id"], m["dir"] == "up", max(1, min(int(m.get("count", 1)), 20))
        pane = (await herdr("pane.get", {"pane_id": pid}))["pane"]
        sc = pane.get("scroll") or {}
        if sc.get("max_offset_from_bottom", 0) > 0:
            off = sc.get("offset_from_bottom", 0) + (3 * count if up else -3 * count)
            off = max(0, min(sc["max_offset_from_bottom"], off))
            await herdr("pane.scroll", {"pane_id": pid, "offset_from_bottom": off})
            st["scroll_offset"] = off
            await ws.send_str(json.dumps({"type": "scroll", "pane_id": pid, "offset": off,
                                          "max": sc["max_offset_from_bottom"]}))
        else:
            fg = (await herdr("pane.process_info", {"pane_id": pid}))["process_info"].get("foreground_processes", [])
            names = {(p.get("name") or "").lstrip("-") for p in fg}
            if pane.get("agent"):  # agents (Claude Code) ask for SGR mouse reports: send real wheel events
                col = max(1, int(m.get("col", 1)))
                row = max(1, int(m.get("row", 1)))
                await herdr("pane.send_text", {"pane_id": pid, "text": f"\x1b[<{64 if up else 65};{col};{row}M" * count})
            elif not names <= SHELLS:  # other full-screen apps (less, man, top): arrow keys, like alternate scroll
                await herdr("pane.send_keys", {"pane_id": pid, "keys": ["up" if up else "down"] * (3 * count)})
        st["burst_until"] = time.monotonic() + BURST_SECONDS
        st["kick"].set()

    async def frame_loop(self, ws: web.WebSocketResponse):
        """Mirror the watched pane: poll its rendered viewport (ANSI) and push on change.

        Input from this client sets st["kick"] (read now) and extends st["burst_until"] (poll fast).
        """
        last = None
        st = self.clients.get(ws)
        while not ws.closed and ws in self.clients:
            pid = st.get("watch")
            if st.pop("resend", False):
                last = None  # (re)opened: send the current screen even if it hasn't changed since we last sent it
            if pid:
                try:
                    if DEMO:
                        import demo
                        text = demo.frame(pid)
                    else:
                        r = await herdr("pane.read", {"pane_id": pid, "source": "visible", "format": "ansi",
                                                      "strip_ansi": False})
                        text = r["read"]["text"]
                    if (pid, text) != last:
                        last = (pid, text)
                        rect = None
                        for lay in self.snapshot.get("layouts", []):
                            for p in lay.get("panes", []):
                                if p["pane_id"] == pid:
                                    rect = p["rect"]
                        await ws.send_str(json.dumps({"type": "frame", "pane_id": pid, "text": text,
                                                      "cols": (rect or {}).get("width"),
                                                      "rows": (rect or {}).get("height"),
                                                      "seq": st.get("input_seq", 0)}))
                except Exception as e:  # noqa: BLE001
                    if last != (pid, "__err__"):
                        last = (pid, "__err__")
                        await ws.send_str(json.dumps({"type": "frame_error", "pane_id": pid, "error": str(e)}))
            interval = FRAME_BURST if time.monotonic() < st.get("burst_until", 0) else FRAME_IDLE
            st["kick"].clear()
            try:
                await asyncio.wait_for(st["kick"].wait(), interval)
            except asyncio.TimeoutError:
                pass


# --------------------------------------------------------------------------- http

def guard(request: web.Request):
    """Local-only and same-origin: blocks other websites and DNS rebinding from driving terminals."""
    host = request.headers.get("Host", "")
    if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        raise web.HTTPForbidden(text="bad host")
    origin = request.headers.get("Origin")
    if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
        raise web.HTTPForbidden(text="bad origin")
    tok = request.headers.get("X-LCARS-Token") or request.query.get("token")
    if not secrets.compare_digest(tok or "", TOKEN):
        raise web.HTTPForbidden(text="bad token")


async def index(request: web.Request):
    host = request.headers.get("Host", "")
    if host not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
        raise web.HTTPForbidden(text="bad host")
    html = ((STATIC / "index.html").read_text().replace("__LCARS_TOKEN__", TOKEN)
            .replace("__LCARS_SETTINGS__", json.dumps(load_settings()))
            .replace("__LCARS_SOUNDS__", json.dumps(custom_sounds())))
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})


async def transcribe(request: web.Request):
    guard(request)
    hub: Hub = request.app["hub"]
    body = await request.read()
    try:
        audio = decode_audio(body, request.content_type)
    except Exception as e:  # noqa: BLE001
        raise web.HTTPBadRequest(text=str(e))
    seconds = len(audio) / 16000
    peak = float(np.abs(audio).max()) if len(audio) else 0.0  # ~0 means a muted / denied mic
    if seconds < 0.25:
        return web.json_response({"text": "", "seconds": seconds, "ms": 0, "sent": False, "peak": peak})
    t = time.time()
    try:
        text = await hub.voice.transcribe(audio)
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=503)
    ms = round((time.time() - t) * 1000)
    pane_id = request.query.get("pane_id")
    sent = False
    if text and pane_id and request.query.get("send", "1") == "1" and not DEMO:
        await herdr("pane.send_text", {"pane_id": pane_id, "text": text + " "})  # no Enter
        sent = True
    log.info("voice %.1fs peak %.3f -> %dms -> %r (pane %s)", seconds, peak, ms, text, pane_id if sent else "-")
    return web.json_response({"text": text, "seconds": round(seconds, 2), "ms": ms, "sent": sent, "peak": round(peak, 3),
                              "pane_id": pane_id if sent else None})


async def save_settings(request: web.Request):
    guard(request)
    try:
        incoming = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="expected JSON")
    merged = clean_settings({**load_settings(), **(incoming if isinstance(incoming, dict) else {})})
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2) + "\n")
    log.info("settings saved %s (origin %s)", incoming, request.headers.get("Origin", "-"))
    await request.app["hub"].broadcast({"type": "settings", "settings": merged})  # keep other windows in step
    return web.json_response(merged)


async def websocket(request: web.Request):
    guard(request)
    hub: Hub = request.app["hub"]
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=1 << 22)
    await ws.prepare(request)
    hub.clients[ws] = {"watch": None, "kick": asyncio.Event(), "burst_until": 0.0, "input_seq": 0}
    await ws.send_str(json.dumps({"type": "board", "board": hub.build_board()}))
    frames = asyncio.create_task(hub.frame_loop(ws))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            kind = m.get("type")
            try:
                st = hub.clients[ws]
                if DEMO and kind != "watch":
                    if kind in ("input", "keys", "wheel"):
                        continue  # typing / scrolling goes nowhere in demo mode
                    raise HerdrError("demo mode: there's no herdr session to change")
                if kind == "watch":
                    st["watch"] = m.get("pane_id")
                    st["resend"] = True
                    st["scroll_offset"] = 0
                    st["kick"].set()
                elif kind in ("input", "keys"):
                    if st.get("scroll_offset"):  # typing while scrolled back: return to the live bottom first
                        await herdr("pane.scroll", {"pane_id": m["pane_id"], "offset_from_bottom": 0})
                        st["scroll_offset"] = 0
                        await ws.send_str(json.dumps({"type": "scroll", "pane_id": m["pane_id"], "offset": 0}))
                    if kind == "input":        # raw bytes from xterm.js (typing, bracketed pastes)
                        await herdr("pane.send_text", {"pane_id": m["pane_id"], "text": m["data"]})
                    else:                      # logical keys: enter, esc, up, ctrl+c ...
                        await herdr("pane.send_keys", {"pane_id": m["pane_id"], "keys": m["keys"]})
                    st["input_seq"] = m.get("seq", st["input_seq"])
                    st["burst_until"] = time.monotonic() + BURST_SECONDS
                    st["kick"].set()
                elif kind == "wheel":
                    await hub.wheel(ws, st, m)
                elif kind == "rename_workspace":
                    label = " ".join(str(m.get("label", "")).split())[:40]
                    if not label:
                        raise HerdrError("workspace name can't be empty")
                    await herdr("workspace.rename", {"workspace_id": m["workspace_id"], "label": label})
                    hub.refresh.set()  # the workspace.renamed event also triggers this
                elif kind == "focus_tab":      # switches what the herdr TUI shows
                    await herdr("tab.focus", {"tab_id": m["tab_id"]})
                elif kind == "new_tab":
                    r = await herdr("tab.create", {"workspace_id": m.get("workspace_id"),
                                                   "label": m.get("label") or "lcars-shell",
                                                   "cwd": m.get("cwd") or str(Path.home() / "Documents"),
                                                   "focus": False})
                    hub.created_tabs.add(r["tab"]["tab_id"])
                    hub.refresh.set()
                    await ws.send_str(json.dumps({"type": "created", "tab_id": r["tab"]["tab_id"],
                                                  "pane_id": r["root_pane"]["pane_id"]}))
                elif kind == "close_tab":  # plain shells only; agent tabs are closed from herdr
                    agent_panes = {a["pane_id"] for a in hub.snapshot.get("agents", [])}
                    tab_panes = [p["pane_id"] for p in hub.snapshot.get("panes", []) if p["tab_id"] == m["tab_id"]]
                    if m["tab_id"] not in hub.created_tabs and (not tab_panes or any(p in agent_panes for p in tab_panes)):
                        raise HerdrError("LCARS only closes plain shell tabs; close agent tabs from herdr")
                    await herdr("tab.close", {"tab_id": m["tab_id"]})
                    hub.created_tabs.discard(m["tab_id"])
                    hub.refresh.set()
            except Exception as e:  # noqa: BLE001 - report to the UI, keep the socket
                await ws.send_str(json.dumps({"type": "toast", "level": "error", "text": str(e)}))
    finally:
        frames.cancel()
        hub.clients.pop(ws, None)
    return ws


async def on_startup(app: web.Application):
    hub: Hub = app["hub"]
    adopt = os.environ.get("LCARS_ADOPT_TABS", "")  # comma-separated tab ids the UI may close (testing)
    hub.created_tabs.update(t for t in adopt.split(",") if t)
    app["tasks"] = [asyncio.create_task(c) for c in
                    (hub.snapshot_loop(), hub.preview_loop(), hub.event_loop(), hub.voice.load())]


async def on_shutdown(app: web.Application):
    hub: Hub = app["hub"]
    for ws in list(hub.clients):
        await ws.close(code=1001, message=b"server shutdown")


async def on_cleanup(app: web.Application):
    for t in app["tasks"]:
        t.cancel()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    app = web.Application(client_max_size=64 << 20)
    app["hub"] = Hub(Voice(MODEL_PATH))
    app.router.add_get("/", index)
    app.router.add_get("/ws", websocket)
    app.router.add_post("/api/transcribe", transcribe)
    app.router.add_post("/api/settings", save_settings)
    app.router.add_static("/static/", STATIC)
    SOUNDS_DIR.mkdir(exist_ok=True)
    app.router.add_static("/sounds/", SOUNDS_DIR)
    app.router.add_static("/vendor/xterm/", ROOT / "node_modules/@xterm/xterm")
    app.router.add_static("/vendor/fonts/", ROOT / "node_modules/@fontsource/antonio/files")
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    app.on_cleanup.append(on_cleanup)
    print(f"LCARS online: http://{HOST}:{PORT}/", flush=True)
    web.run_app(app, host=HOST, port=PORT, print=None, shutdown_timeout=2)


if __name__ == "__main__":
    main()
