"""Minimal PCM audio container and helpers (stdlib only).

All clips are mono, 16-bit signed PCM. Backends may return any sample rate;
the assembler resamples to the project rate so clips from different engines
can be concatenated.
"""
from __future__ import annotations

import array
import math
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioClip:
    samples: array.array  # typecode 'h' (int16)
    sample_rate: int

    @classmethod
    def silence(cls, ms: int, sample_rate: int) -> "AudioClip":
        n = int(sample_rate * ms / 1000)
        return cls(array.array("h", bytes(2 * n)), sample_rate)

    @classmethod
    def from_wav(cls, path) -> "AudioClip":
        """Read a 16-bit WAV from a path or a binary file object."""
        src = path if hasattr(path, "read") else str(path)
        with wave.open(src, "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError(f"{path}: only 16-bit WAV is supported")
            raw = w.readframes(w.getnframes())
            channels, rate = w.getnchannels(), w.getframerate()
        data = array.array("h")
        data.frombytes(raw)
        if sys.byteorder == "big":
            data.byteswap()
        if channels > 1:  # downmix
            data = array.array(
                "h",
                (int(sum(data[i : i + channels]) / channels) for i in range(0, len(data), channels)),
            )
        return cls(data, rate)

    def active_rms(self, floor: int = 330) -> float:
        """RMS over samples louder than `floor` (about -40 dBFS), i.e. ignoring silence."""
        total, n = 0, 0
        for s in self.samples:
            if s > floor or s < -floor:
                total += s * s
                n += 1
        return math.sqrt(total / n) if n else 0.0

    def normalize(self, target_dbfs: float) -> "AudioClip":
        """Set speech loudness (active RMS, dBFS) to `target_dbfs`."""
        rms = self.active_rms()
        if rms <= 0:
            return self
        return self.gain(target_dbfs - 20 * math.log10(rms / 32768))

    def match_loudness(self, reference: "AudioClip") -> "AudioClip":
        """Scale so speech loudness equals `reference` (effects must not change volume)."""
        mine, ref = self.active_rms(), reference.active_rms()
        if mine <= 0 or ref <= 0:
            return self
        return self.gain(20 * math.log10(ref / mine))

    @property
    def duration_s(self) -> float:
        return len(self.samples) / self.sample_rate

    def gain(self, db: float) -> "AudioClip":
        """Change level by `db`. Peaks above about -1 dBFS are rounded off with a soft
        knee instead of hard-clipped, which avoids crackle on loud emotions."""
        if db == 0:
            return self
        k = 10 ** (db / 20)
        knee = 29200.0  # ~ -1 dBFS
        room = 32767.0 - knee

        def limit(v: float) -> int:
            a = abs(v)
            if a <= knee:
                return int(v)
            soft = knee + room * math.tanh((a - knee) / room)
            return int(soft if v > 0 else -soft)

        out = array.array("h", (limit(s * k) for s in self.samples))
        return AudioClip(out, self.sample_rate)

    def resample(self, rate: int) -> "AudioClip":
        """Linear-interpolation resampler; good enough for assembly, not mastering."""
        if rate == self.sample_rate or not self.samples:
            return AudioClip(self.samples, rate) if not self.samples else self
        ratio = self.sample_rate / rate
        n_out = int(len(self.samples) / ratio)
        src, last = self.samples, len(self.samples) - 1
        out = array.array("h", bytes(2 * n_out))
        for i in range(n_out):
            pos = i * ratio
            j = int(pos)
            frac = pos - j
            a = src[j]
            b = src[j + 1] if j < last else a
            out[i] = int(a + (b - a) * frac)
        return AudioClip(out, rate)

    def write_wav(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = array.array("h", self.samples)
        if sys.byteorder == "big":
            data.byteswap()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(data.tobytes())
        return path


def concat(clips: list[AudioClip], sample_rate: int) -> AudioClip:
    out = array.array("h")
    for c in clips:
        out.extend(c.resample(sample_rate).samples)
    return AudioClip(out, sample_rate)


def fade_edges(clip: AudioClip, ms: int = 5) -> AudioClip:
    """Short fade in/out to avoid clicks at clip boundaries."""
    n = min(int(clip.sample_rate * ms / 1000), len(clip.samples) // 2)
    s = array.array("h", clip.samples)
    for i in range(n):
        k = i / n
        s[i] = int(s[i] * k)
        s[-1 - i] = int(s[-1 - i] * k)
    return AudioClip(s, clip.sample_rate)


def semitones_to_ratio(st: float) -> float:
    return math.pow(2.0, st / 12.0)


def pitch_shift(clip: AudioClip, semitones: float) -> AudioClip:
    """Shift pitch while keeping duration, using ffmpeg (asetrate + atempo).

    A fallback for backends that cannot shift pitch themselves. Raises
    RuntimeError if ffmpeg is missing; the caller decides whether to warn or fail.
    """
    if semitones == 0 or not clip.samples:
        return clip
    r = semitones_to_ratio(semitones)
    sr = clip.sample_rate
    # atempo accepts 0.5..100 per stage; 1/r stays inside that for +-12 semitones
    return ffmpeg_filter(clip, f"asetrate={sr * r:.0f},aresample={sr},atempo={1 / r:.6f}")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_filter(clip: AudioClip, graph: str, complex_graph: bool = False) -> AudioClip:
    """Run `clip` through an ffmpeg audio filter (-af) or filter graph (-filter_complex).

    Output keeps the clip's sample rate, mono, 16-bit. Raises RuntimeError if
    ffmpeg is missing or the filter fails.
    """
    if not clip.samples:
        return clip
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg not found")
    with tempfile.TemporaryDirectory() as td:
        src, dst = Path(td) / "in.wav", Path(td) / "out.wav"
        clip.write_wav(src)
        flt = ["-filter_complex", graph] if complex_graph else ["-af", graph]
        cmd = [exe, "-v", "error", "-y", "-i", str(src), *flt, "-ar", str(clip.sample_rate), "-ac", "1",
               "-c:a", "pcm_s16le", str(dst)]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg filter failed: {proc.stderr.strip()[:300]}")
        return AudioClip.from_wav(dst)
