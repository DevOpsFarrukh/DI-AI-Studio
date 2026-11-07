"""Adapters for local, open-source TTS engines invoked as command-line tools.

Each adapter only builds a command line and reads back a WAV file, so it has
no link-time dependency on the engine. If the engine is missing, check()
raises BackendUnavailable with an install hint.

    espeak-ng : GPL-3.0, formant synthesis, many languages; `apt install espeak-ng`
    piper     : MIT, neural VITS voices, offline; `pip install piper-tts` + a .onnx voice model
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from ..audio import AudioClip
from ..models import Utterance
from .base import BackendUnavailable, TTSBackend, register_backend


def _run(cmd: list[str], stdin: str | None = None) -> None:
    proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()[:500]}")


class EspeakBackend(TTSBackend):
    name = "espeak"
    supports = frozenset({"rate", "pitch_semitones", "volume_db"})

    def _exe(self) -> str:
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        if not exe:
            raise BackendUnavailable("espeak-ng not found. Install with: sudo apt install espeak-ng")
        return exe

    def check(self) -> None:
        self._exe()

    def synthesize(self, utt: Utterance) -> AudioClip:
        o, p = utt.voice.backend_options, utt.prosody
        wpm = int(float(o.get("wpm", 165)) * p.rate)
        pitch = int(max(0, min(99, float(o.get("pitch", 50)) + p.pitch_semitones * 4)))
        amp = int(max(0, min(200, 100 * 10 ** (p.volume_db / 20))))
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "u.wav"
            _run([self._exe(), "-v", str(o.get("voice", "en-us")), "-s", str(wpm),
                  "-p", str(pitch), "-a", str(amp), "-w", str(out), "--stdin"], stdin=utt.text)
            return AudioClip.from_wav(out)


class PiperBackend(TTSBackend):
    name = "piper"
    supports = frozenset({"rate", "volume_db"})  # pitch is handled by the engine's fallback

    def _exe(self) -> str:
        # $PIPER_BIN, then PATH, then next to the running interpreter (an un-activated venv)
        candidates = [os.environ.get("PIPER_BIN"), shutil.which("piper"),
                      str(Path(sys.executable).parent / "piper")]
        for c in candidates:
            if c and Path(c).is_file() and os.access(c, os.X_OK):
                return c
        raise BackendUnavailable("piper not found. Install with: pip install piper-tts")

    def __init__(self) -> None:
        self._voices: dict[str, object] = {}  # model path -> loaded PiperVoice
        try:  # in-process API: loads each model once, much faster for long scripts
            from piper import PiperVoice, SynthesisConfig
            self._api = (PiperVoice, SynthesisConfig)
        except ImportError:
            self._api = None

    def check(self) -> None:
        if self._api is None:
            self._exe()

    def _synth_inprocess(self, utt: Utterance, model: str) -> AudioClip:
        PiperVoice, SynthesisConfig = self._api
        o = utt.voice.backend_options
        if model not in self._voices:
            self._voices[model] = PiperVoice.load(model)
        cfg = SynthesisConfig(
            speaker_id=o.get("speaker"),
            length_scale=float(o.get("length_scale", 1.0)) / utt.prosody.rate,
            volume=10 ** (utt.prosody.volume_db / 20),
        )
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            self._voices[model].synthesize_wav(utt.text, w, syn_config=cfg)
        buf.seek(0)
        return AudioClip.from_wav(buf)

    def synthesize(self, utt: Utterance) -> AudioClip:
        o = utt.voice.backend_options
        model = o.get("model")
        if not model or not Path(model).exists():
            raise BackendUnavailable(f"piper model not found for voice '{utt.voice.id}': {model}")
        if self._api is not None:
            return self._synth_inprocess(utt, model)
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "u.wav"
            cmd = [self._exe(), "-m", str(model), "-f", str(out),
                   "--length-scale", f"{float(o.get('length_scale', 1.0)) / utt.prosody.rate:.3f}",
                   "--volume", f"{10 ** (utt.prosody.volume_db / 20):.3f}",
                   "--sentence-silence", "0"]
            if "speaker" in o:
                cmd += ["--speaker", str(o["speaker"])]
            _run(cmd, stdin=utt.text)
            return AudioClip.from_wav(out)


register_backend("espeak", EspeakBackend)
register_backend("piper", PiperBackend)
