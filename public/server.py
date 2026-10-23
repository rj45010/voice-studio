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

API_VERSION = 2
PORT = int(os.environ.get("VOICE_STUDIO_PORT", "7861"))
ORIGINS = {o for o in os.environ.get(
    "VOICE_STUDIO_ORIGINS",
    "https://voice-studio.seemslegit.workers.dev,http://127.0.0.1:7861,http://localhost:7861",
).split(",") if o}
IDLE_UNLOAD_SECONDS = 600
HOME = Path.home() / ".voice-studio"
SPEED_FILE = HOME / "speed.json"
WEB_DIR = Path(__file__).resolve().parent

state = {"model": None, "asr": None, "last_used": 0.0, "loading": False}
ASR_MODEL = "openai/whisper-small"
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


APPLE_ASR = WEB_DIR / "transcribe-mac"
LOCALES = {"en": ("en-US", "english"), "hi": ("hi-IN", "hindi")}
LANGUAGES = {"English", "Hindi"}


def transcribe(wav: np.ndarray, sr: int, lang: str | None = None) -> str:
    locale, whisper_lang = LOCALES.get(lang or "", (None, None))
    if APPLE_ASR.is_file():
        import subprocess
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            sf.write(f.name, wav, sr)
            out = subprocess.run([str(APPLE_ASR), f.name] + ([locale] if locale else []), capture_output=True, text=True, timeout=120)
        if out.returncode == 0:
            return " ".join(s["text"] for s in json.loads(out.stdout)["segments"])
        # Unsupported locale or no speech assets on this Mac: fall through to Whisper rather than fail.
        print(f"[voice-studio] Apple speech failed: {out.stderr.strip()[-200:]}", file=sys.stderr)
    with model_lock:
        if state["asr"] is None:
            from transformers import pipeline
            state["asr"] = pipeline("automatic-speech-recognition", model=ASR_MODEL, device="cpu")
        state["last_used"] = time.time()
        kwargs = {"generate_kwargs": {"language": whisper_lang}} if whisper_lang else {}
        return state["asr"]({"raw": wav, "sampling_rate": sr}, **kwargs)["text"].strip()


def unload_when_idle() -> None:
    while True:
        time.sleep(30)
        busy = any(j["status"] in ("queued", "running") or j.get("redo") is not None for j in jobs.values())
        loaded = state["model"] is not None or state["asr"] is not None
        if loaded and not busy and time.time() - state["last_used"] > IDLE_UNLOAD_SECONDS:
            with model_lock:
                state["model"] = state["asr"] = None
            print("[voice-studio] model unloaded after idle", file=sys.stderr)


def busy() -> bool:
    return any(j["status"] in ("queued", "running") or j.get("redo") is not None for j in jobs.values())


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


MOODS = {"normal", "excited", "calm", "deadpan", "serious", "curious"}
TARGET_RMS = 0.08  # speech level every sentence is brought to, so mood samples recorded louder or softer don't jump
MAX_GAIN = 10 ** (9 / 20)
FADE_SECONDS = 0.01


def speech_rms(audio: np.ndarray, sr: int) -> float:
    frame = max(1, int(0.02 * sr))
    n = len(audio) // frame
    if n == 0:
        return float(np.sqrt(np.mean(audio**2))) if len(audio) else 0.0
    rms = np.sqrt(np.mean(audio[: n * frame].reshape(n, frame) ** 2, axis=1))
    loud = rms[rms >= rms.max() * 10 ** (-30 / 20)]
    return float(np.sqrt(np.mean(loud**2))) if loud.size else 0.0


def level(audio: np.ndarray, sr: int) -> np.ndarray:
    rms = speech_rms(audio, sr)
    out = audio * min(MAX_GAIN, TARGET_RMS / rms) if rms > 0 else audio.copy()
    fade = min(int(FADE_SECONDS * sr), len(out) // 2)
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)
        out[:fade] *= ramp
        out[-fade:] *= ramp[::-1]
    return out.astype(np.float32)


def wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 0.98:
        audio = audio * (0.98 / peak)
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV")
    return buf.getvalue()


def prompt_for(job: dict, model: OmniVoice, mood: str):
    if mood not in job["prompts"]:
        wav, sr, text = job["moods"][mood]
        job["prompts"][mood] = model.create_voice_clone_prompt(
            ref_audio=(torch.from_numpy(wav).float().unsqueeze(0), sr), ref_text=text
        )
    return job["prompts"][mood]


def synth(job: dict, model: OmniVoice, s: dict) -> np.ndarray:
    kwargs = {"speed": s["speed"]} if s["speed"] != 1.0 else {}
    audio = model.generate(text=s["text"], voice_clone_prompt=prompt_for(job, model, s["mood"]), language=job["language"], **kwargs)[0]
    return level(audio.astype(np.float32), model.sampling_rate)


def run_job_v2(job: dict) -> None:
    try:
        model, load_seconds = get_model()
        job["sr"] = model.sampling_rate
        job["status"] = "running"
        started = time.time()
        for s in job["sentences"]:
            if job["cancel"]:
                job["status"] = "cancelled"
                return
            s["status"] = "running"
            try:
                s["audio"] = synth(job, model, s)
                s["status"] = "done"
            except Exception as exc:  # one bad sentence shouldn't lose the rest; the page offers redo
                s["status"], s["error"] = "error", f"{type(exc).__name__}: {exc}"
                print(f"[voice-studio] sentence failed: {s['error']}", file=sys.stderr)
            job["done"] += 1
            state["last_used"] = time.time()
        job["status"] = "done"
        chars = sum(len(s["text"]) for s in job["sentences"])
        save_speed((time.time() - started) / max(1, chars), load_seconds)
    except Exception as exc:
        job["status"] = "error"
        job["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[voice-studio] job failed: {job['error']}", file=sys.stderr)


def run_redo(job: dict, n: int) -> None:
    s = job["sentences"][n]
    try:
        model, _ = get_model()
        s["audio"] = synth(job, model, s)
        s["status"], s["error"] = "done", None
    except Exception as exc:
        s["status"], s["error"] = "error", f"{type(exc).__name__}: {exc}"
    finally:
        job["redo"] = None
        state["last_used"] = time.time()


def decode_wav(b64: str) -> tuple[np.ndarray, int]:
    wav, sr = sf.read(io.BytesIO(base64.b64decode(b64)), dtype="float32")
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    return wav, sr


def parse_job_v2(body: dict) -> dict:
    """Validate a v2 request; raises ValueError with a message the page can show."""
    language = body.get("language")
    if language is not None and language not in LANGUAGES:
        raise ValueError("language must be English, Hindi or empty")
    raw = body.get("sentences")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 200:
        raise ValueError("send 1 to 200 sentences")
    moods_in = body.get("moods")
    if not isinstance(moods_in, dict) or not moods_in:
        raise ValueError("moods are required")
    sentences = []
    for item in raw:
        text = str(item.get("text", "")).strip()
        mood, pause, speed = item.get("mood"), float(item.get("pause", 0.35)), float(item.get("speed", 1.0))
        if not 1 <= len(text) <= 600:
            raise ValueError("each sentence must be 1 to 600 characters")
        if mood not in moods_in or mood not in MOODS:
            raise ValueError(f"no sample for mood {mood!r}")
        if not 0 <= pause <= 2 or not 0.7 <= speed <= 1.3:
            raise ValueError("pause must be 0 to 2 s and speed 0.7 to 1.3")
        sentences.append({"text": text, "mood": mood, "pause": pause, "speed": speed, "status": "waiting", "error": None, "audio": None})
    moods = {}
    for mood in {s["mood"] for s in sentences}:
        m = moods_in[mood]
        text = str(m.get("text", "")).strip()
        if not text:
            raise ValueError(f"sample text for {mood} is empty")
        wav, sr = decode_wav(m["wav_b64"])
        moods[mood] = (wav, sr, text)
    return {"status": "queued", "done": 0, "language": language, "sentences": sentences, "moods": moods,
            "prompts": {}, "cancel": False, "redo": None, "error": None, "v": 2}


def joined(job: dict) -> np.ndarray:
    sr, parts = job["sr"], []
    for s in job["sentences"]:
        if s["audio"] is not None:
            parts += [s["audio"], np.zeros(int(s["pause"] * sr), dtype=np.float32)]
    return np.concatenate(parts[:-1]) if parts else np.zeros(0, dtype=np.float32)


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
                "ok": True, "api": API_VERSION, "device": DEVICE, "loaded": state["model"] is not None,
                "loading": state["loading"], "speed": load_speed(), "busy": busy(),
            })
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{32})(?:/(audio)|/sentences/(\d+))?", self.path)
        if m and m.group(1) in jobs:
            job = jobs[m.group(1)]
            if job.get("v") == 2:
                if m.group(3) is not None:
                    n = int(m.group(3))
                    if n >= len(job["sentences"]) or job["sentences"][n]["audio"] is None:
                        return self.json(404, {"error": "not ready"})
                    return self.reply(200, wav_bytes(job["sentences"][n]["audio"], job["sr"]), "audio/wav")
                if m.group(2):
                    audio = joined(job) if "sr" in job else np.zeros(0)
                    return self.reply(200, wav_bytes(audio, job["sr"]), "audio/wav") if audio.size else self.json(404, {"error": "not ready"})
                return self.json(200, {
                    "status": job["status"], "done": job["done"], "error": job["error"], "total": len(job["sentences"]),
                    "redo": job["redo"], "sentences": [{"status": s["status"], "error": s["error"]} for s in job["sentences"]],
                })
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
        if self.path.split("?")[0] == "/api/transcribe":
            return self.transcribe()
        m = re.fullmatch(r"/api/jobs/([0-9a-f]{32})/(cancel|sentences/(\d+)/redo)", self.path)
        if m:
            return self.job_action(m.group(1), m.group(2), m.group(3))
        if self.path != "/api/jobs":
            return self.json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 20_000_000:
                return self.json(413, {"error": "request too large"})
            body = json.loads(self.rfile.read(length))
            if body.get("api") == 2:
                return self.start_v2(body)
            text, ref_text = str(body["text"]).strip(), str(body["ref_text"]).strip()
            ref_wav, ref_sr = sf.read(io.BytesIO(base64.b64decode(body["ref_audio"])), dtype="float32")
        except (KeyError, ValueError, TypeError, sf.LibsndfileError) as exc:
            return self.json(400, {"error": f"bad request: {exc}"})
        if ref_wav.ndim > 1:
            ref_wav = ref_wav.mean(axis=1)
        sentences = split_sentences(text)
        if not sentences or not ref_text:
            return self.json(400, {"error": "text and ref_text are required"})
        if busy():
            return self.json(409, {"error": "already generating; wait for the current job"})
        job_id = uuid.uuid4().hex
        jobs.clear()  # keep only the latest result in memory
        jobs[job_id] = {"status": "queued", "done": 0, "text": text, "sentences": sentences}
        threading.Thread(target=run_job, args=(jobs[job_id], ref_wav, ref_sr, ref_text), daemon=True).start()
        self.json(202, {"id": job_id, "total": len(sentences)})

    def start_v2(self, body: dict) -> None:
        try:
            job = parse_job_v2(body)
        except (KeyError, ValueError, TypeError, AttributeError, sf.LibsndfileError) as exc:
            return self.json(400, {"error": str(exc) or "bad request"})
        if busy():
            return self.json(409, {"error": "already generating; wait for the current job"})
        job_id = uuid.uuid4().hex
        jobs.clear()  # keep only the latest result in memory
        jobs[job_id] = job
        threading.Thread(target=run_job_v2, args=(job,), daemon=True).start()
        self.json(202, {"id": job_id, "total": len(job["sentences"])})

    def job_action(self, job_id: str, action: str, n: str | None) -> None:
        job = jobs.get(job_id)
        if not job or job.get("v") != 2:
            return self.json(404, {"error": "job not found"})
        if action == "cancel":
            job["cancel"] = True
            return self.json(200, {"ok": True})
        i = int(n)
        if i >= len(job["sentences"]):
            return self.json(404, {"error": "no such sentence"})
        if busy() or "sr" not in job:
            return self.json(409, {"error": "wait until generation finishes"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            mood = json.loads(self.rfile.read(length)).get("mood") if length else None
        except ValueError:
            mood = None
        s = job["sentences"][i]
        if mood is not None:
            if mood not in job["moods"]:
                return self.json(400, {"error": f"no sample for mood {mood!r} in this job"})
            s["mood"] = mood
        s["status"], s["error"], job["redo"] = "running", None, i
        threading.Thread(target=run_redo, args=(job, i), daemon=True).start()
        self.json(202, {"ok": True})

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

    def transcribe(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 50_000_000:
            return self.json(413, {"error": "file too large"})
        try:
            wav, sr = sf.read(io.BytesIO(self.rfile.read(length)), dtype="float32")
        except sf.LibsndfileError:
            return self.json(415, {"error": "send WAV audio"})
        if wav.ndim > 1:
            wav = wav.mean(axis=1)
        lang = re.search(r"[?&]lang=(en|hi)\b", self.path)
        try:
            return self.json(200, {"text": transcribe(wav, sr, lang and lang.group(1))})
        except Exception as exc:  # model missing offline, etc.; the page falls back to typing
            return self.json(500, {"error": f"{type(exc).__name__}: {exc}"})

    def log_message(self, fmt: str, *args) -> None:
        pass


if __name__ == "__main__":
    threading.Thread(target=unload_when_idle, daemon=True).start()
    print(f"[voice-studio] listening on http://127.0.0.1:{PORT} (device {DEVICE})", file=sys.stderr)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
