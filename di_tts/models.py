"""Core data types shared by every stage of the pipeline.

These are plain dataclasses with no engine-specific fields, so any backend
(built-in, Piper, eSpeak, a cloud API, a future in-house model) can consume them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class Prosody:
    """Engine-neutral delivery parameters.

    rate             speaking-speed multiplier (1.0 = voice default)
    pitch_semitones  shift relative to the voice's base pitch
    volume_db        gain relative to the voice's default loudness
    pause_after_ms   silence inserted after the utterance
    pause_scale      multiplier on the gap between sentences of one line (base 120 ms)
    clause_pause_ms  if > 0, sentences are split at , ; : — and this much silence is
                     inserted between clauses (each clause gets its own intonation)
    rhythm_variation multiplier on the engine's timing randomness (Piper noise_w);
                     < 1 is steadier, > 1 is more animated. Engines may ignore it.

    Every default is a no-op, so older voice and emotion files behave as before.
    """

    rate: float = 1.0
    pitch_semitones: float = 0.0
    volume_db: float = 0.0
    pause_after_ms: int = 300
    pause_scale: float = 1.0
    clause_pause_ms: int = 0
    rhythm_variation: float = 1.0

    def combine(self, other: "Prosody") -> "Prosody":
        """Layer `other` (e.g. an emotion) on top of this (e.g. a voice default)."""
        return Prosody(
            rate=self.rate * other.rate,
            pitch_semitones=self.pitch_semitones + other.pitch_semitones,
            volume_db=self.volume_db + other.volume_db,
            pause_after_ms=max(self.pause_after_ms, other.pause_after_ms),
            pause_scale=self.pause_scale * other.pause_scale,
            clause_pause_ms=self.clause_pause_ms + other.clause_pause_ms,
            rhythm_variation=self.rhythm_variation * other.rhythm_variation,
        )

    def scaled(self, intensity: float) -> "Prosody":
        """Interpolate from neutral (intensity 0) to the full effect (intensity 1)."""

        def lerp(v: float) -> float:
            return 1.0 + (v - 1.0) * intensity

        return Prosody(
            rate=lerp(self.rate),
            pitch_semitones=self.pitch_semitones * intensity,
            volume_db=self.volume_db * intensity,
            # interpolate from the default 300 ms, so intensity 0 leaves the voice's pause alone
            pause_after_ms=int(round(300 + (self.pause_after_ms - 300) * intensity)),
            pause_scale=lerp(self.pause_scale),
            clause_pause_ms=int(round(self.clause_pause_ms * intensity)),
            rhythm_variation=lerp(self.rhythm_variation),
        )


@dataclass(frozen=True)
class TextStyle:
    """Punctuation shaping applied before synthesis.

    Piper's intonation follows punctuation: measured on 2026-11-07, ending a sentence
    with "!" instead of "." made it about 11% faster and widened the pitch range by
    about 50%. Ellipses had no effect, so pauses are inserted as real silence instead
    (see Prosody.clause_pause_ms).

    exclaim_at  at intensity >= this, sentence-final "." becomes "!"
    soften_at   at intensity >= this, "!" becomes "."   (calmer, flatter delivery)
    """

    exclaim_at: float | None = None
    soften_at: float | None = None


@dataclass(frozen=True)
class EffectSpec:
    """A post-processing audio effect (see di_tts/effects.py); amount 0..1 at intensity 1."""

    type: str
    amount: float = 1.0


@dataclass(frozen=True)
class Emotion:
    name: str
    prosody: Prosody = field(default_factory=Prosody)
    description: str = ""
    label: str = ""
    category: str = "basic"  # basic | advanced | style
    text: TextStyle = field(default_factory=TextStyle)
    effects: tuple[EffectSpec, ...] = ()

    @property
    def display(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


@dataclass(frozen=True)
class VoiceProfile:
    """A character voice: which backend renders it, plus default delivery."""

    id: str
    display_name: str
    backend: str
    backend_options: dict[str, Any] = field(default_factory=dict)
    prosody: Prosody = field(default_factory=Prosody)
    aliases: tuple[str, ...] = ()
    #: backend used when `backend` is unavailable on this machine (e.g. "tone")
    fallback_backend: str | None = None


@dataclass(frozen=True)
class ScriptLine:
    """One parsed line of a script, before voice/emotion resolution."""

    speaker: str
    text: str
    emotion: str = "neutral"
    intensity: float = 1.0
    line_no: int = 0


@dataclass(frozen=True)
class Utterance:
    """A fully-resolved unit of work handed to a backend."""

    text: str  # text as sent to the engine (after punctuation shaping)
    voice: VoiceProfile
    prosody: Prosody
    source: ScriptLine  # source.text keeps the original wording for the timeline
    effects: tuple[EffectSpec, ...] = ()  # amounts already scaled by intensity

    def with_text(self, text: str) -> "Utterance":
        return replace(self, text=text)
