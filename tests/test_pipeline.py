import json
import tempfile
import unittest
from pathlib import Path

from di_tts import Prosody, TTSEngine, parse_script
from di_tts.script import chunk_text

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"


class ParseTests(unittest.TestCase):
    def test_speaker_emotion_intensity_and_continuation(self):
        lines = parse_script("# c\nALICE (happy:0.5): Hi.\n  more\nBOB: Yo.\n")
        self.assertEqual([(l.speaker, l.emotion, l.intensity) for l in lines],
                         [("alice", "happy", 0.5), ("bob", "neutral", 1.0)])
        self.assertEqual(lines[0].text, "Hi. more")

    def test_unprefixed_first_line_goes_to_narrator(self):
        self.assertEqual(parse_script("Just text.")[0].speaker, "narrator")

    def test_chunking_respects_limit(self):
        text = "One. " + "word " * 200 + "end."
        chunks = list(chunk_text(text, max_chars=50))
        self.assertTrue(all(len(c) <= 51 for c in chunks))
        self.assertEqual(" ".join(chunks).split(), text.split())


class ProsodyTests(unittest.TestCase):
    def test_intensity_scaling_and_combine(self):
        happy = Prosody(rate=1.2, pitch_semitones=2, volume_db=2)
        half = happy.scaled(0.5)
        self.assertAlmostEqual(half.rate, 1.1)
        self.assertEqual(half.pitch_semitones, 1)
        combined = Prosody(rate=0.5).combine(half)
        self.assertAlmostEqual(combined.rate, 0.55)


class EngineTests(unittest.TestCase):
    def setUp(self):
        # the tone backend keeps tests fast and dependency-free
        self.engine = TTSEngine.from_config_dir(CONFIG, backend_override="tone")

    def test_render_demo_produces_audio_and_timeline(self):
        result = self.engine.render((ROOT / "examples" / "demo_script.txt").read_text())
        self.assertGreater(result.audio.duration_s, 3)
        self.assertEqual([e.voice for e in result.timeline], ["narrator", "alice", "bob", "alice", "narrator"])
        starts = [e.start_s for e in result.timeline]
        self.assertEqual(starts, sorted(starts))
        with tempfile.TemporaryDirectory() as td:
            wav, cue = result.save(Path(td) / "x.wav")
            self.assertGreater(wav.stat().st_size, 1000)
            self.assertEqual(len(json.loads(cue.read_text())["entries"]), 5)

    def test_sad_is_slower_than_neutral(self):
        n = self.engine.render("BOB: one two three four five").audio.duration_s
        s = self.engine.render("BOB (sad): one two three four five").audio.duration_s
        self.assertGreater(s, n)

    def test_unknown_speaker_and_emotion_fail_fast(self):
        with self.assertRaises(KeyError):
            self.engine.render("ZED: hi")
        with self.assertRaises(KeyError):
            self.engine.render("ALICE (furious): hi")


if __name__ == "__main__":
    unittest.main()
