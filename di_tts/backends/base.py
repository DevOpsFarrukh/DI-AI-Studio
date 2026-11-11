"""Backend contract.

A backend turns one `Utterance` into an `AudioClip`. That is the only thing
the rest of the system knows about speech synthesis, so engines are swappable.

Backends declare which prosody controls they honour natively. For the rest,
the engine applies a generic fallback (for example an ffmpeg pitch shift) or records a warning.
Volume is always applied by the engine after loudness normalisation, never by backends.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from ..audio import AudioClip
from ..models import Utterance


class BackendUnavailable(RuntimeError):
    """Raised when a backend's dependency (binary, model, package) is missing."""


class TTSBackend(ABC):
    name: str = "base"
    #: subset of {"rate", "pitch_semitones", "rhythm_variation", "emotion"} handled inside synthesize()
    supports: frozenset[str] = frozenset()

    def check(self) -> None:
        """Raise BackendUnavailable if the backend cannot run on this machine."""

    @abstractmethod
    def synthesize(self, utt: Utterance) -> AudioClip: ...


_FACTORIES: dict[str, Callable[[], TTSBackend]] = {}


def register_backend(name: str, factory: Callable[[], TTSBackend]) -> None:
    _FACTORIES[name] = factory


def create_backend(name: str) -> TTSBackend:
    if name not in _FACTORIES:
        raise KeyError(f"unknown backend '{name}'. Registered: {', '.join(sorted(_FACTORIES))}")
    return _FACTORIES[name]()


def registered_backends() -> list[str]:
    return sorted(_FACTORIES)
