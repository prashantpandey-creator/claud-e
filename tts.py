"""tts — Kokoro: the voice, rendered locally, that doesn't sound like a phone menu.

Why this exists, in numbers: every one of the 180 Apple voices installed on
this machine is the compact tier — the enhanced/premium ones are a manual
download nobody has done — and a compact voice sounds synthetic no matter how
the rate and pitch are tuned. Kokoro-82M (Apache 2.0) runs on-device, faster
than real time on Apple Silicon, and is the same lane the servers can run, so
every product speaks with one voice.

Contract (the mascot shells this):
    python3.10 tts.py "text" /tmp/out.wav [voice]
        -> prints one line:  ok <seconds_of_audio> <render_seconds>
        -> exit 0 with a 24 kHz 16-bit mono wav at the path, or exit 1

    python3.10 tts.py --check      # is the lane usable? prints why not
    python3.10 tts.py --voices     # the English voices, best-for-us first
    python3.10 tts.py --serve      # loopback server holding the model WARM

The server exists because of one measurement: the model loads in 1.32s and
renders three sentences in 2.25s — so cold, every line pays ~4s before the
first sound, and warm it pays ~2. A companion that answers four seconds late
sounds broken however good the voice is.

Runs under python3.10 because onnxruntime has no 3.14 wheel yet.
Model files live in ~/.claude/meditation/models (downloaded once, ~340 MB).
"""
from __future__ import annotations

import os
import sys
import time

MODELS = os.path.expanduser("~/.claude/meditation/models")
MODEL = os.path.join(MODELS, "kokoro-v1.0.onnx")
VOICES = os.path.join(MODELS, "voices-v1.0.bin")

# Calm, low, unhurried first. af_* female, am_* male, b* British.
# Male, calm and low first — the owner asked for a male voice, and a default
# nobody chose is still a choice. MEDITATE_TTS_VOICE overrides.
PREFERRED = ["am_michael", "bm_george", "am_adam", "am_eric",
             "af_heart", "bf_emma", "af_sarah"]
DEFAULT_VOICE = os.environ.get("MEDITATE_TTS_VOICE", PREFERRED[0])
SPEED = float(os.environ.get("MEDITATE_TTS_SPEED", "0.92"))   # a touch unhurried


def _why_not() -> str:
    if sys.version_info >= (3, 13):
        return "needs python3.10 (onnxruntime has no wheel for this python)"
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile    # noqa: F401
    except Exception as e:
        return "python packages missing: %s" % e
    if not os.path.exists(MODEL):
        return "model missing: %s" % MODEL
    if not os.path.exists(VOICES):
        return "voices missing: %s" % VOICES
    return ""


def render(text: str, out_path: str, voice: str = DEFAULT_VOICE) -> tuple:
    """Returns (audio_seconds, render_seconds). Raises on failure."""
    from kokoro_onnx import Kokoro
    import soundfile as sf
    t0 = time.time()
    k = Kokoro(MODEL, VOICES)
    samples, rate = k.create(text, voice=voice, speed=SPEED, lang="en-us")
    sf.write(out_path, samples, rate, subtype="PCM_16")
    return (len(samples) / float(rate), time.time() - t0)


PORT = int(os.environ.get("MEDITATE_TTS_PORT", "7712"))


def serve() -> int:
    why = _why_not()
    if why:
        print("cannot serve: %s" % why, file=sys.stderr)
        return 1
    import json
    import tempfile
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from kokoro_onnx import Kokoro
    import soundfile as sf
    k = Kokoro(MODEL, VOICES)              # warm — the whole point
    # first inference also pays graph warm-up; do it now, not on the first line
    k.create("Ready.", voice=DEFAULT_VOICE, speed=SPEED, lang="en-us")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):          # quiet
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/ping":
                self._json(200, {"ok": True, "voice": DEFAULT_VOICE})
            else:
                self._json(404, {"ok": False})

        def do_POST(self):
            if self.path != "/tts":
                return self._json(404, {"ok": False})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                req = json.loads(self.rfile.read(n) or b"{}")
                text = str(req.get("text", ""))[:2000].strip()
                voice = str(req.get("voice") or DEFAULT_VOICE)
                if not text:
                    return self._json(400, {"ok": False, "err": "no text"})
                t0 = time.time()
                samples, rate = k.create(text, voice=voice, speed=SPEED,
                                         lang="en-us")
                fd, path = tempfile.mkstemp(prefix="casper-", suffix=".wav")
                os.close(fd)
                sf.write(path, samples, rate, subtype="PCM_16")
                self._json(200, {"ok": True, "wav": path,
                                 "audio_s": round(len(samples) / rate, 2),
                                 "render_s": round(time.time() - t0, 2)})
            except Exception as e:                      # noqa: BLE001
                self._json(500, {"ok": False, "err": str(e)[:200]})

    srv = HTTPServer(("127.0.0.1", PORT), H)
    print("kokoro warm on 127.0.0.1:%d (voice %s)" % (PORT, DEFAULT_VOICE))
    srv.serve_forever()
    return 0


def main(argv) -> int:
    if argv and argv[0] == "--serve":
        return serve()
    if argv and argv[0] == "--check":
        why = _why_not()
        print(why or "ok")
        return 1 if why else 0
    if argv and argv[0] == "--voices":
        for v in PREFERRED:
            print(v)
        return 0
    if len(argv) < 2:
        print("usage: tts.py <text> <out.wav> [voice]", file=sys.stderr)
        return 1
    why = _why_not()
    if why:
        print("unavailable: %s" % why, file=sys.stderr)
        return 1
    text, out_path = argv[0], argv[1]
    voice = argv[2] if len(argv) > 2 else DEFAULT_VOICE
    try:
        audio_s, render_s = render(text, out_path, voice)
    except Exception as e:
        print("failed: %s" % str(e)[:200], file=sys.stderr)
        return 1
    print("ok %.2f %.2f" % (audio_s, render_s))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
