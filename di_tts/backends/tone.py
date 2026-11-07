"""Dependency-free placeholder backend.

It does not produce intelligible speech. It renders a speech-like tone per
syllable, honouring pitch and rate (the engine applies volume). Use it to test the full pipeline
(parsing, voices, emotions, timing, assembly) on any machine, including CI.
"""
from __future__ import annotations

import array
import math
import re

from ..audio import AudioClip, semitones_to_ratio
from ..models import Utterance
from .base import TTSBackend, register_backend

_VOWEL_GROUPS = re.compile(r"[aeiouy]+", re.I)


def _syllables(word: str) -> int:
    return max(1, len(_VOWEL_GROUPS.findall(word)))


class ToneBackend(TTSBackend):
    name = "tone"
    supports = frozenset({"rate", "pitch_semitones"})

    def __init__(self, sample_rate: int = 22050) -> None:
        self.sample_rate = sample_rate

    def synthesize(self, utt: Utterance) -> AudioClip:
        opts = utt.voice.backend_options
        base_hz = float(opts.get("base_pitch_hz", 140)) * semitones_to_ratio(utt.prosody.pitch_semitones)
        syl_ms = float(opts.get("syllable_ms", 160)) / max(utt.prosody.rate, 0.1)
        amp = 0.3
        sr = self.sample_rate
        out = array.array("h")
        words = re.findall(r"[\w']+|[,.;:!?]", utt.text)
        for wi, w in enumerate(words):
            if w in ",;:":
                out.extend(bytes(2 * int(sr * syl_ms * 1.2 / 1000)))
                continue
            if w in ".!?":
                continue
            n_syl = _syllables(w)
            for si in range(n_syl):
                # gentle declination across the utterance + per-syllable contour
                f = base_hz * (1.0 - 0.15 * wi / max(len(words), 1)) * (1.05 if si == 0 else 1.0)
                n = int(sr * syl_ms / 1000)
                for i in range(n):
                    env = math.sin(math.pi * i / n)
                    t = i / sr
                    v = math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t)
                    out.append(max(-32768, min(32767, int(32767 * amp * env * v / 1.3))))
            out.extend(bytes(2 * int(sr * syl_ms * 0.25 / 1000)))  # inter-word gap
        if utt.text.rstrip().endswith("?"):
            pass  # rising intonation could be modelled here
        return AudioClip(out, sr)


register_backend("tone", ToneBackend)
