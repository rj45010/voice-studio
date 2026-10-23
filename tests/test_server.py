import base64
import io
import sys
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "public"))
import server  # noqa: E402

SR = 24000


def wav_b64(seconds=1.0, amp=0.1):
    t = np.arange(int(SR * seconds)) / SR
    buf = io.BytesIO()
    sf.write(buf, (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32), SR, format="WAV")
    return base64.b64encode(buf.getvalue()).decode()


def body(**over):
    b = {"api": 2, "language": None, "moods": {"normal": {"text": "hi there", "wav_b64": wav_b64()}},
         "sentences": [{"text": "Hello.", "mood": "normal", "pause": 0.35, "speed": 1.0}]}
    b.update(over)
    return b


class FakeModel:
    sampling_rate = SR

    def create_voice_clone_prompt(self, ref_audio, ref_text):
        return ref_text

    def generate(self, text, voice_clone_prompt, language=None, **kw):
        if text == "boom.":
            raise RuntimeError("bad sentence")
        return [np.full(SR // 2, 0.01 * len(text), dtype=np.float32)]


class ParseTests(unittest.TestCase):
    def test_valid(self):
        job = server.parse_job_v2(body())
        self.assertEqual(job["sentences"][0]["status"], "waiting")
        self.assertIn("normal", job["moods"])

    def test_rejects(self):
        cases = [
            body(language="French"),
            body(sentences=[]),
            body(sentences=[{"text": "x" * 601, "mood": "normal"}]),
            body(sentences=[{"text": "Hi.", "mood": "deadpan"}]),
            body(sentences=[{"text": "Hi.", "mood": "normal", "pause": 5}]),
            body(sentences=[{"text": "Hi.", "mood": "normal", "speed": 2}]),
            body(moods={}),
            body(moods={"normal": {"text": " ", "wav_b64": wav_b64()}}),
        ]
        for b in cases:
            with self.assertRaises(ValueError):
                server.parse_job_v2(b)

    def test_unused_moods_not_decoded(self):
        b = body()
        b["moods"]["calm"] = {"text": "x", "wav_b64": "not base64 audio"}
        self.assertNotIn("calm", server.parse_job_v2(b)["moods"])


class AudioTests(unittest.TestCase):
    def test_level_brings_quiet_and_loud_together(self):
        quiet = server.level(np.full(SR, 0.02, dtype=np.float32), SR)
        loud = server.level(np.full(SR, 0.3, dtype=np.float32), SR)
        self.assertAlmostEqual(server.speech_rms(loud, SR), server.TARGET_RMS, delta=0.005)
        self.assertLessEqual(server.speech_rms(quiet, SR), 0.02 * server.MAX_GAIN + 1e-3)

    def test_fades_and_silence(self):
        out = server.level(np.full(SR, 0.2, dtype=np.float32), SR)
        self.assertEqual(out[0], 0.0)
        self.assertTrue(np.all(server.level(np.zeros(100, dtype=np.float32), SR) == 0))

    def test_peak_limit(self):
        data, _ = sf.read(io.BytesIO(server.wav_bytes(np.full(100, 1.5, dtype=np.float32), SR)))
        self.assertLessEqual(np.abs(data).max(), 0.99)

    def test_join_uses_pauses(self):
        job = {"sr": SR, "sentences": [{"audio": np.ones(100, np.float32), "pause": 0.5}, {"audio": np.ones(100, np.float32), "pause": 0.9}]}
        self.assertEqual(len(server.joined(job)), 200 + int(0.5 * SR))


class JobTests(unittest.TestCase):
    def setUp(self):
        server.jobs.clear()
        server.get_model = lambda: (FakeModel(), None)
        server.save_speed = lambda *a: None

    def run_job(self, texts, cancel_after=None):
        b = body(sentences=[{"text": t, "mood": "normal"} for t in texts])
        job = server.parse_job_v2(b)
        if cancel_after is not None:
            job["cancel"] = True
        server.jobs["a" * 32] = job
        server.run_job_v2(job)
        return job

    def test_one_failure_keeps_the_rest(self):
        job = self.run_job(["Hi.", "boom.", "Bye."])
        self.assertEqual([s["status"] for s in job["sentences"]], ["done", "error", "done"])
        self.assertEqual(job["status"], "done")

    def test_cancel(self):
        job = self.run_job(["Hi.", "Bye."], cancel_after=0)
        self.assertEqual(job["status"], "cancelled")
        self.assertFalse(server.busy())

    def test_redo(self):
        job = self.run_job(["Hi.", "boom."])
        job["sentences"][1]["text"] = "fixed."
        job["redo"] = 1
        self.assertTrue(server.busy())
        server.run_redo(job, 1)
        self.assertEqual(job["sentences"][1]["status"], "done")
        self.assertIsNone(job["redo"])
        self.assertFalse(server.busy())


if __name__ == "__main__":
    unittest.main()
