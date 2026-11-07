"""Data-driven registries for voices and emotions.

Voices and emotions live in JSON files, so adding one is a data change,
not a code change. Drop a new file into `voices/` or add an entry to
`emotions.json` and it is picked up on the next run.
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import Emotion, Prosody, VoiceProfile

_PROSODY_KEYS = {"rate", "pitch_semitones", "volume_db", "pause_after_ms"}


def _prosody(d: dict | None) -> Prosody:
    d = d or {}
    unknown = set(d) - _PROSODY_KEYS
    if unknown:
        raise ValueError(f"unknown prosody keys: {sorted(unknown)}")
    return Prosody(**d)


class EmotionRegistry:
    def __init__(self) -> None:
        self._emotions: dict[str, Emotion] = {"neutral": Emotion("neutral", Prosody(), "Baseline delivery")}

    def register(self, emotion: Emotion) -> None:
        self._emotions[emotion.name.lower()] = emotion

    def get(self, name: str) -> Emotion:
        try:
            return self._emotions[name.lower()]
        except KeyError:
            raise KeyError(f"unknown emotion '{name}'. Known: {', '.join(sorted(self._emotions))}") from None

    def names(self) -> list[str]:
        return sorted(self._emotions)

    @classmethod
    def from_file(cls, path: str | Path) -> "EmotionRegistry":
        reg = cls()
        for name, spec in json.loads(Path(path).read_text()).items():
            reg.register(Emotion(name, _prosody(spec.get("prosody")), spec.get("description", "")))
        return reg


class VoiceRegistry:
    def __init__(self) -> None:
        self._voices: dict[str, VoiceProfile] = {}
        self._aliases: dict[str, str] = {}

    def register(self, voice: VoiceProfile) -> None:
        self._voices[voice.id] = voice
        for a in (voice.id, *voice.aliases):
            self._aliases[a.lower()] = voice.id

    def get(self, name: str) -> VoiceProfile:
        vid = self._aliases.get(name.lower())
        if vid is None:
            raise KeyError(f"no voice for speaker '{name}'. Known: {', '.join(sorted(self._aliases))}")
        return self._voices[vid]

    def all(self) -> list[VoiceProfile]:
        return list(self._voices.values())

    @classmethod
    def from_dir(cls, path: str | Path) -> "VoiceRegistry":
        reg = cls()
        for f in sorted(Path(path).glob("*.json")):
            d = json.loads(f.read_text())
            opts = dict(d.get("backend_options", {}))
            # relative model paths are resolved against the voice file's folder
            if isinstance(opts.get("model"), str) and not Path(opts["model"]).is_absolute():
                opts["model"] = str((f.parent / opts["model"]).resolve())
            reg.register(
                VoiceProfile(
                    id=d["id"],
                    display_name=d.get("display_name", d["id"]),
                    backend=d["backend"],
                    backend_options=opts,
                    prosody=_prosody(d.get("prosody")),
                    aliases=tuple(d.get("aliases", [])),
                    fallback_backend=d.get("fallback_backend"),
                )
            )
        return reg
