import os
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
except Exception:  # fastapi/httpx not installed: skip web tests
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi test client not available")
class WebApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["DI_TTS_OUTPUT"] = tempfile.mkdtemp()
        os.environ["DI_TTS_MAX_CHARS"] = "200"
        from di_tts.web import app as web
        web.engine.backend_override = "tone"  # fast, no models needed
        cls.web, cls.client = web, TestClient(web.app)

    def test_health_and_voices(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        v = self.client.get("/api/voices").json()
        self.assertIn("neutral", v["emotions"])
        self.assertTrue(v["voices"])

    def test_say_returns_playable_audio(self):
        r = self.client.post("/api/say", json={"text": "Hello there.", "voice": "alice", "emotion": "happy"})
        self.assertEqual(r.status_code, 200, r.text)
        audio = self.client.get(r.json()["audio_url"])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.content[:4], b"RIFF")

    def test_render_script_timeline(self):
        r = self.client.post("/api/render", json={"script": "NARRATOR: Hi.\nBOB (sad): Bye."})
        self.assertEqual([e["voice"] for e in r.json()["timeline"]], ["narrator", "bob"])

    def test_errors(self):
        self.assertEqual(self.client.post("/api/say", json={"text": "x", "voice": "zed"}).status_code, 422)
        self.assertEqual(self.client.post("/api/say", json={"text": "x" * 201}).status_code, 413)
        self.assertEqual(self.client.post("/api/render", json={"script": "# only a comment"}).status_code, 422)
        self.assertEqual(self.client.get("/api/audio/../../etc/passwd").status_code, 404)

    def test_emotion_details_are_described_honestly(self):
        v = self.client.get("/api/voices").json()
        details = {d["name"]: d for d in v["emotion_details"]}
        self.assertEqual(set(details), set(v["emotions"]))
        self.assertEqual(details["neutral"]["category"], "basic")
        self.assertIn("realization", details["whisper"])
        self.assertTrue(v["not_supported"])  # crying, laughing, ... are listed as unavailable

    def test_say_with_analysis(self):
        r = self.client.post("/api/say", json={"text": "Hello there.", "voice": "bob", "emotion": "sad",
                                               "intensity": 0.5, "analyze": True}).json()
        from di_tts import analysis
        if analysis.available():
            self.assertIn("speech_s", r["metrics"])
        else:
            self.assertNotIn("metrics", r)

    def test_api_key(self):
        self.web.API_KEY = "secret"
        try:
            self.assertEqual(self.client.get("/api/voices").status_code, 401)
            self.assertEqual(self.client.get("/api/voices", headers={"X-API-Key": "secret"}).status_code, 200)
        finally:
            self.web.API_KEY = None


if __name__ == "__main__":
    unittest.main()
