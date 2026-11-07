"""Tests for the emotion system: configuration, shaping, effects, honesty labels and real audio.

Unit tests run anywhere (tone backend). The AudioTests class renders real speech with
Piper and measures it; it is skipped when Piper, the models, numpy or ffmpeg are missing.
"""
import itertools
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from di_tts import Prosody, ScriptLine, TTSEngine, parse_script
from di_tts import analysis, effects
from di_tts.audio import ffmpeg_available
from di_tts.models import EffectSpec, TextStyle
from di_tts.registry import EmotionRegistry
from di_tts.script import shape_text, split_clauses

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
SENTENCE = "I can't believe you actually did that."
EXPECTED = {"neutral", "happy", "excited", "sad", "angry", "calm", "fearful", "serious", "curious",
            "nervous", "dramatic", "urgent", "whisper", "cinematic", "news"}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.reg = EmotionRegistry.from_file(CONFIG / "emotions.json")

    def test_all_emotions_present_with_categories(self):
        self.assertEqual(set(self.reg.names()), EXPECTED)
        self.assertEqual(self.reg.all()[0].name, "neutral")
        for e in self.reg.all():
            self.assertIn(e.category, ("basic", "advanced", "style"), e.name)
            self.assertTrue(e.description, e.name)

    def test_every_emotion_changes_delivery(self):
        neutral = Prosody()
        for e in self.reg.all():
            if e.name == "neutral":
                continue
            changes = e.prosody != neutral or e.effects or e.text != TextStyle()
            self.assertTrue(changes, f"{e.name} is only a label")

    def test_rejects_unknown_effect_keys_and_category(self):
        bad = [{"x": {"effects": [{"type": "autotune"}]}},
               {"x": {"prosody": {"speed": 2}}},
               {"x": {"category": "weird"}},
               {"x": {"text": {"shout_at": 0.5}}},
               {"x": {"effects": [{"type": "punch", "amount": 2}]}}]
        for spec in bad:
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(spec, f)
            with self.assertRaises(ValueError, msg=spec):
                EmotionRegistry.from_file(f.name)

    def test_old_style_emotion_file_still_loads(self):
        old = {"neutral": {"description": "Baseline", "prosody": {}},
               "happy": {"description": "x", "prosody": {"rate": 1.1, "pitch_semitones": 2.0}}}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(old, f)
        reg = EmotionRegistry.from_file(f.name)
        self.assertEqual(reg.get("happy").prosody.rate, 1.1)
        self.assertEqual(reg.get("happy").effects, ())


class ProsodyAndTextTests(unittest.TestCase):
    def test_new_prosody_fields_scale_and_combine(self):
        p = Prosody(pause_scale=3.0, clause_pause_ms=400, rhythm_variation=0.5).scaled(0.5)
        self.assertAlmostEqual(p.pause_scale, 2.0)
        self.assertEqual(p.clause_pause_ms, 200)
        self.assertAlmostEqual(p.rhythm_variation, 0.75)
        c = Prosody(pause_scale=2.0, clause_pause_ms=100).combine(Prosody(pause_scale=1.5, clause_pause_ms=50))
        self.assertEqual((c.pause_scale, c.clause_pause_ms), (3.0, 150))
        self.assertEqual(Prosody().scaled(0.7), Prosody())  # defaults stay no-ops

    def test_exclaim_and_soften(self):
        style = TextStyle(exclaim_at=0.5)
        self.assertEqual(shape_text("We did it. Yes.", style, 0.4), "We did it. Yes.")
        self.assertEqual(shape_text("We did it. Yes.", style, 0.6), "We did it! Yes!")
        self.assertEqual(shape_text("Ask Dr. Smith now.", style, 1), "Ask Dr. Smith now!")
        self.assertEqual(shape_text("Wait... what.", style, 1), "Wait... what!")
        self.assertEqual(shape_text("WE DID IT!!", TextStyle(soften_at=0.3), 0.5), "WE DID IT.")

    def test_clause_and_phrase_split(self):
        self.assertEqual(split_clauses("Bob, did you hear that?"), ["Bob,", "did you hear that?"])
        self.assertEqual(split_clauses(SENTENCE), [SENTENCE])
        self.assertEqual(split_clauses(SENTENCE, phrase_split=True), ["I can't believe you actually", "did that."])
        self.assertEqual(split_clauses("Too short here.", phrase_split=True), ["Too short here."])


class EffectGraphTests(unittest.TestCase):
    def test_graph_building(self):
        self.assertIsNone(effects.filter_graph([]))
        self.assertIsNone(effects.filter_graph([EffectSpec("punch", 0.0)]))
        g = effects.filter_graph([EffectSpec("tremble", 0.5), EffectSpec("whisper", 0.5), EffectSpec("reverb", 0.5)])
        self.assertTrue(g.startswith("[0:a]vibrato"))
        self.assertIn("[d1]", g)  # whisper's internal labels are made unique
        self.assertEqual(g.count("aformat=sample_fmts=s16"), 2)  # between stages only
        self.assertFalse(g.rstrip().endswith("]"))  # last stage unlabelled

    def test_validate(self):
        with self.assertRaises(ValueError):
            effects.validate(EffectSpec("nope"))


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = TTSEngine.from_config_dir(CONFIG, backend_override="tone")

    def test_intensity_zero_equals_neutral(self):
        for name in EXPECTED:
            u = self.engine.resolve(ScriptLine("alice", "We did it. Really!", name, 0.0, 1))
            n = self.engine.resolve(ScriptLine("alice", "We did it. Really!", "neutral", 1.0, 1))
            self.assertEqual(u.prosody, n.prosody, name)
            self.assertEqual(u.effects, (), name)
            if name != "whisper":  # whisper softens "!" at any intensity by design
                self.assertEqual(u.text, n.text, name)

    def test_effects_scale_with_intensity(self):
        u = self.engine.resolve(ScriptLine("bob", "Hi.", "angry", 0.5, 1))
        self.assertEqual(u.effects, (EffectSpec("punch", 0.5),))

    def test_clause_pauses_lengthen_and_timeline_keeps_original_text(self):
        line = "Well, we tried, we failed, and that was that."
        calm = self.engine.render_lines([ScriptLine("alice", line, "neutral", 1, 1)])
        dram = self.engine.render_lines([ScriptLine("alice", line, "dramatic", 1, 1)])
        self.assertGreater(dram.timeline[0].end_s, calm.timeline[0].end_s + 1.0)
        excited = self.engine.render_lines([ScriptLine("alice", "We did it.", "excited", 1, 1)])
        self.assertEqual(excited.timeline[0].text, "We did it.")  # not the shaped "We did it!"

    def test_realization_labels_are_honest(self):
        self.assertEqual(self.engine.realization("neutral", "piper")["level"], "baseline")
        r = self.engine.realization("whisper", "piper")
        self.assertEqual(r["level"], "simulated")
        self.assertIn("whisper", r["effects"])
        self.assertIn("not genuine", r["notes"])
        self.assertEqual(self.engine.realization("sad", "tone")["level"], "placeholder")
        self.engine.emotions._emotions["plain"] = self.engine.emotions.get("neutral").__class__(
            "plain", Prosody(rate=0.9))
        self.assertEqual(self.engine.realization("plain", "piper")["level"], "approximated")

    def test_missing_ffmpeg_is_reported_not_silent(self):
        with mock.patch("di_tts.engine.ffmpeg_filter", side_effect=RuntimeError("ffmpeg not found")):
            res = self.engine.render_lines([ScriptLine("alice", "Careful now.", "whisper", 1, 1)])
        self.assertTrue(any("without its audio effects" in w for w in res.warnings), res.warnings)
        self.assertGreater(res.audio.duration_s, 0.3)

    def test_empty_line_does_not_eat_previous_pause(self):
        res = self.engine.render_lines([ScriptLine("alice", "Hello there.", "neutral", 1, 1),
                                        ScriptLine("bob", "   ", "neutral", 1, 2)])
        self.assertEqual(len(res.timeline), 1)
        self.assertTrue(any("nothing to speak" in w for w in res.warnings))

    def test_backward_compatible_script(self):
        lines = parse_script("NARRATOR: The station was quiet.\nALICE (happy:0.6): Bob?\nBOB (sad:0.8): Nothing.")
        res = self.engine.render_lines(lines)
        self.assertEqual([e.emotion for e in res.timeline], ["neutral", "happy", "sad"])


def _real_audio_ready() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return analysis.available() and ffmpeg_available() and (ROOT / "models" / "en_US-amy-medium.onnx").exists()


@unittest.skipUnless(_real_audio_ready(), "needs piper, voice models, numpy and ffmpeg")
class AudioTests(unittest.TestCase):
    """Measures the generated audio. Piper's timing is slightly random (about ±5%),
    so thresholds sit well above that noise."""

    @classmethod
    def setUpClass(cls):
        cls.engine = TTSEngine.from_config_dir(CONFIG)
        cls.m = {}
        for e in cls.engine.emotions.all():
            res = cls.engine.render_lines([ScriptLine("alice", SENTENCE, e.name, 1.0, 1)])
            assert not res.warnings, res.warnings
            assert res.audio.active_rms() > 500, f"{e.name} is (nearly) silent"
            cls.m[e.name] = analysis.measure(res.audio)

    def st(self, a, b):
        return 12 * math.log2(self.m[a]["pitch_hz"] / self.m[b]["pitch_hz"])

    def test_signature_differences(self):
        m = self.m
        self.assertGreater(m["sad"]["speech_s"], 1.3 * m["neutral"]["speech_s"])
        self.assertLess(m["sad"]["pitch_hz"], m["neutral"]["pitch_hz"])
        self.assertLess(m["sad"]["brightness_hz"], 0.85 * m["neutral"]["brightness_hz"])
        self.assertGreater(self.st("excited", "neutral"), 3.0)
        self.assertGreater(m["excited"]["loudness_db"], m["neutral"]["loudness_db"] + 2.5)
        self.assertGreater(m["angry"]["loudness_db"], m["neutral"]["loudness_db"] + 2.5)
        self.assertGreater(m["angry"]["brightness_hz"], 1.1 * m["neutral"]["brightness_hz"])
        self.assertLess(m["urgent"]["speech_s"], 0.88 * m["neutral"]["speech_s"])
        self.assertLess(m["whisper"]["voiced_ratio"], 0.45)
        self.assertGreater(m["dramatic"]["speech_s"], 1.3 * m["neutral"]["speech_s"])

    def test_emotions_are_mutually_distinguishable(self):
        def differs(a, b):
            return (abs(math.log(a["speech_s"] / b["speech_s"])) > math.log(1.12)
                    or abs(12 * math.log2(a["pitch_hz"] / b["pitch_hz"])) > 1.5
                    or abs(a["pitch_range_st"] - b["pitch_range_st"]) > 2.0
                    or abs(a["loudness_db"] - b["loudness_db"]) > 2.0
                    or abs(math.log(a["brightness_hz"] / b["brightness_hz"])) > math.log(1.1)
                    or abs(a["voiced_ratio"] - b["voiced_ratio"]) > 0.15)
        weak = [(a, b) for a, b in itertools.combinations(self.m, 2) if not differs(self.m[a], self.m[b])]
        # 0 weak pairs in 9 trial runs on 2026-11-07; allow 1 for Piper's randomness
        self.assertLessEqual(len(weak), 1, weak)

    def test_intensity_changes_audio_monotonically(self):
        def run(name, level):
            res = self.engine.render_lines([ScriptLine("alice", SENTENCE, name, level, 1)])
            return analysis.measure(res.audio)
        sad = [run("sad", x)["speech_s"] for x in (0.2, 1.0)]
        self.assertGreater(sad[1], 1.3 * sad[0])
        exc = [run("excited", x)["pitch_hz"] for x in (0.2, 1.0)]
        self.assertGreater(12 * math.log2(exc[1] / exc[0]), 2.0)


if __name__ == "__main__":
    unittest.main()
