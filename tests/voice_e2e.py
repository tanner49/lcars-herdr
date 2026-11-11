"""Browser-side push-to-talk, end to end, with no real microphone.

Headless Chrome's fake media device plays a synthesized WAV as the "mic"; the test holds Right Option
(dispatched key events), releases it, and checks the transcript landed in the target pane without Enter.
Pass a pane YOU created (a scratch shell) — this types into it.

    .venv/bin/python tests/voice_e2e.py --pane w7:pN
"""

import argparse
import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from cdp import chrome

PHRASE = "Computer, show me every blocked agent."


async def main(pane: str, hold: float):
    with tempfile.TemporaryDirectory() as d:
        aiff, wav = Path(d) / "v.aiff", Path(d) / "v.wav"
        subprocess.run(["say", "-o", str(aiff), PHRASE], check=True)
        # pad with silence so the looping fake device doesn't start mid-word on the first frame
        # Chrome's fake device wants plain 16-bit PCM with no metadata chunk
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-af", "adelay=300,apad=pad_dur=1",
                        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:a", "+bitexact",
                        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(wav)], check=True)
        flags = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                 f"--use-file-for-fake-audio-capture={wav}", "--autoplay-policy=no-user-gesture-required",
                 # the sandboxed audio service can't read the file; run audio in-process for this throwaway profile
                 "--disable-features=AudioServiceOutOfProcess"]
        async with chrome(extra_flags=flags) as page:
            await page.cmd("Page.navigate", url=f"http://127.0.0.1:7477/#pane={pane}")
            await page.idle(2.5)
            key = lambda t: f"window.dispatchEvent(new KeyboardEvent('{t}', {{code: 'AltRight', key: 'Alt', altKey: {str(t == 'keydown').lower()}}}))"
            await page.eval(key("keydown"))
            await page.idle(hold)
            await page.eval(key("keyup"))
            for _ in range(40):
                await page.idle(0.25)
                state = await page.eval("document.getElementById('vox-state').textContent")
                text = await page.eval("document.getElementById('vox-text').textContent")
                if state.endswith("READY") and ("→" in text or "no speech" in text):
                    break
            print("vox-state:", state)
            print("vox-text: ", text)
            frame = await page.eval("(() => { const b = []; const t = document.querySelector('.xterm-rows'); "
                                    "return t ? [...t.children].map(r => r.textContent).filter(s => s.trim()).slice(-2) : b; })()")
            print("pane tail:", json.dumps(frame))
            if page.exceptions:
                print("page exceptions:", page.exceptions)
            ok = "blocked agent" in text.lower()
            print("RESULT:", "PASS" if ok else "FAIL")
            return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pane", required=True)
    ap.add_argument("--hold", type=float, default=4.5)
    a = ap.parse_args()
    raise SystemExit(asyncio.run(main(a.pane, a.hold)))
