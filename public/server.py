import base64
import io
import json
import os
import re
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np
import soundfile as sf
import torch
from omnivoice import OmniVoice

PORT = int(os.environ.get("VOICE_STUDIO_PORT", "7861"))
ORIGINS = {o for o in os.environ.get(
    "VOICE_STUDIO_ORIGINS",
    "https://voice-studio.seemslegit.workers.dev,http://127.0.0.1:7861,http://localhost:7861",
).split(",") if o}
IDLE_UNLOAD_SECONDS = 600
HOME = Path.home() / ".voice-studio"
SPEED_FILE = HOME / "speed.json"
WEB_DIR = Path(__file__).resolve().parent

state = {"model": None, "last_used": 0.0, "loading": False}
model_lock = threading.Lock()
jobs: dict[str, dict] = {}


def pick_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    # MPS dies silently loading this model on 8 GB Apple Silicon; only use it with headroom.
    if torch.backends.mps.is_available() and os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") >= 16e9:
        return "mps"
    return "cpu"


DEVICE = os.environ.get("VOICE_STUDIO_DEVICE") or pick_device()


def load_speed() -> dict:
    try:
        return json.loads(SPEED_FILE.read_text())
    except (OSError, ValueError):
        # Rough first guess (8 GB M1 on CPU); replaced by real measurements after the first job.
        return {"sec_per_char": 2.5, "load_seconds": 60}


def save_speed(sec_per_char: float, load_seconds: float | None) -> None:
    speed = load_speed()
    speed["sec_per_char"] = round(0.5 * speed["sec_per_char"] + 0.5 * sec_per_char, 3)
    if load_seconds is not None:
        speed["load_seconds"] = round(load_seconds)
    HOME.mkdir(exist_ok=True)
    SPEED_FILE.write_text(json.dumps(speed))


def get_model() -> tuple[OmniVoice, float | None]:
    with model_lock:
        if state["model"] is None:
            state["loading"] = True
            started = time.time()
            dtype = torch.float32 if DEVICE == "cpu" else torch.float16
            state["model"] = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map=DEVICE, dtype=dtype)
            state["loading"] = False
            state["last_used"] = time.time()
            return state["model"], time.time() - started
        state["last_used"] = time.time()
        return state["model"], None


def unload_when_idle() -> None:
    while True:
        time.sleep(30)
        busy = any(j["status"] == "running" for j in jobs.values())
        if state["model"] is not None and not busy and time.time() - state["last_used"] > IDLE_UNLOAD_SECONDS:
            with model_lock:
                state["model"] = None
            print("[voice-studio] model unloaded after idle", file=sys.stderr)


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?।])\s+", text.strip())
    return [p for p in (s.strip() for s in parts) if p]


def run_job(job: dict, ref_wav: np.ndarray, ref_sr: int, ref_text: str) -> None:
    try:
        model, load_seconds = get_model()
        job["status"] = "running"
        started = time.time()
        prompt = model.create_voice_clone_prompt(
            ref_audio=(torch.from_numpy(ref_wav).float().unsqueeze(0), ref_sr), ref_text=ref_text
        )
        pieces = []
        gap = np.zeros(int(0.25 * model.sampling_rate), dtype=np.float32)
        for i, sentence in enumerate(job["sentences"]):
            audio = model.generate(text=sentence, voice_clone_prompt=prompt)[0]
            pieces += [audio.astype(np.float32), gap]
            job["done"] = i + 1
            state["last_used"] = time.time()
        buf = io.BytesIO()
        sf.write(buf, np.concatenate(pieces[:-1]), model.sampling_rate, format="WAV")
        job["audio"] = buf.getvalue()
        job["status"] = "done"
        save_speed((time.time() - started) / max(1, len(job["text"])), load_seconds)
    except Exception as exc:  # surfaced to the page instead of killing the worker thread
        job["status"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[voice-studio] job failed: {job['error']}", file=sys.stderr)


class Handler(BaseHTTPRequestHandler):
    def cors(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

    def reply(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json(self, code: int, data: dict) -> None:
        self.reply(code, json.dumps(data).encode())

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in ORIGINS

    def do_OPTIONS(self) -> None:
        self.send_response(204 if self.origin_ok() else 403)
        self.cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if not self.origin_ok():
            return self.json(403, {"error": "origin not allowed"})
        if self.path == "/api/status":
            return self.json(200, {
                "ok": True, "device": DEVICE, "loaded": state["model"] is not None,
                "loading": state["loading"], "speed": load_speed(),
                "busy": any(j["status"] in ("queued", "running") for j in jobs.values()),
            })
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(/audio)?", self.path)
        if m and m.group(1) in jobs:
            job = jobs[m.group(1)]
            if m.group(2):
                return self.reply(200, job["audio"], "audio/wav") if job.get("audio") else self.json(404, {"error": "not ready"})
            return self.json(200, {k: job.get(k) for k in ("status", "done", "error")} | {"total": len(job["sentences"])})
        if self.path in ("/", "/index.html") and (WEB_DIR / "index.html").exists():
            return self.reply(200, (WEB_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
        self.json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self.origin_ok():
            return self.json(403, {"error": "origin not allowed"})
        if self.path == "/api/convert":
            return self.convert()
        if self.path != "/api/jobs":
            return self.json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20_000_000:
                return self.json(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length))
            text, ref_text = str(body["text"]).strip(), str(body["ref_text"]).strip()
            ref_wav, ref_sr = sf.read(io.BytesIO(base64.b64decode(body["ref_audio"])), dtype="float32")
        except (KeyError, ValueError, TypeError, sf.LibsndfileError) as exc:
            return self.json(400, {"error": f"bad request: {exc}"})
        if ref_wav.ndim > 1:
            ref_wav = ref_wav.mean(axis=1)
        sentences = split_sentences(text)
        if not sentences or not ref_text:
            return self.json(400, {"error": "text and ref_text are required"})
        if any(j["status"] in ("queued", "running") for j in jobs.values()):
            return self.json(409, {"error": "already generating; wait for the current job"})
        job_id = uuid.uuid4().hex
        jobs.clear()  # keep only the latest result in memory
        jobs[job_id] = {"status": "queued", "done": 0, "text": text, "sentences": sentences}
        threading.Thread(target=run_job, args=(jobs[job_id], ref_wav, ref_sr, ref_text), daemon=True).start()
        self.json(202, {"id": job_id, "total": len(sentences)})

    def convert(self) -> None:
        """Turn formats the browser can't decode (AIFF/AIFC from QuickTime, etc.) into 24 kHz mono WAV."""
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 50_000_000:
            return self.json(413, {"error": "file too large"})
        try:
            wav, sr = sf.read(io.BytesIO(self.rfile.read(length)), dtype="float32")
        except sf.LibsndfileError:
            return self.json(415, {"error": "unsupported audio format; export as WAV or M4A"})
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        if sr != 24000:
            import librosa
            wav = librosa.resample(wav, orig_sr=sr, target_sr=24000)
        buf = io.BytesIO()
        sf.write(buf, wav, 24000, format="WAV", subtype="PCM_16")
        self.reply(200, buf.getvalue(), "audio/wav")

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(target=unload_when_idle, daemon=True).start()
    print(f"[voice-studio] listening on http://127.0.0.1:{PORT} (device {DEVICE})", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
