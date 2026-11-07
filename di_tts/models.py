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

    rate            speaking-speed multiplier (1.0 = voice default)
    pitch_semitones shift relative to the voice's base pitch
    volume_db       gain relative to the voice's default loudness
    pause_after_ms  silence inserted after the utterance
    """

    rate: float = 1.0
    pitch_semitones: float = 0.0
    volume_db: float = 0.0
    pause_after_ms: int = 300

    def combine(self, other: "Prosody") -> "Prosody":
        """Layer `other` (e.g. an emotion) on top of this (e.g. a voice default)."""
        return Prosody(
            rate=self.rate * other.rate,
            pitch_semitones=self.pitch_semitones + other.pitch_semitones,
            volume_db=self.volume_db + other.volume_db,
            pause_after_ms=max(self.pause_after_ms, other.pause_after_ms),
        )

    def scaled(self, intensity: float) -> "Prosody":
        """Interpolate from neutral (intensity 0) to the full effect (intensity 1)."""
        return Prosody(
            rate=1.0 + (self.rate - 1.0) * intensity,
            pitch_semitones=self.pitch_semitones * intensity,
            volume_db=self.volume_db * intensity,
            pause_after_ms=self.pause_after_ms,
        )


@dataclass(frozen=True)
class Emotion:
    name: str
    prosody: Prosody = field(default_factory=Prosody)
    description: str = ""


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

    text: str
    voice: VoiceProfile
    prosody: Prosody
    source: ScriptLine

    def with_text(self, text: str) -> "Utterance":
        return replace(self, text=text)
