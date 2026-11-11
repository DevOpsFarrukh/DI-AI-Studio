"""Objective acoustic measurements of a clip (used by the Emotion Lab and audio tests).

These numbers let us check that emotions really differ in the generated audio,
rather than trusting that a request succeeded. They are coarse indicators, not
an emotion classifier.

Requires numpy (installed with piper-tts). Without numpy, `available()` is False
and callers skip metrics.
"""
from __future__ import annotations

from .audio import AudioClip

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised only without numpy
    np = None


def available() -> bool:
    return np is not None


def _speech_span(x, sr, frame=0.02):
    """Trim leading/trailing silence; returns the speech portion.

    The threshold is relative to the loudest frame (-35 dB), so quiet deliveries
    (sad, whisper) are not trimmed more than loud ones.
    """
    n = max(1, int(sr * frame))
    frames = len(x) // n
    if frames == 0:
        return x
    rms = np.sqrt(np.mean(x[: frames * n].reshape(frames, n) ** 2, axis=1))
    thresh = max(rms.max() * 10 ** (-35 / 20), 1e-4)
    voiced = np.nonzero(rms > thresh)[0]
    if len(voiced) == 0:
        return x[:0]
    return x[voiced[0] * n : (voiced[-1] + 1) * n]


def _f0_track(x, sr, fmin=60.0, fmax=450.0, frame=0.04, hop=0.01):
    n, h = int(sr * frame), int(sr * hop)
    lo, hi = int(sr / fmax), int(sr / fmin)
    win = np.hanning(n)
    f0, periodic = [], 0
    total = 0
    for i in range(0, len(x) - n, h):
        f = x[i : i + n] * win
        e = np.dot(f, f)
        if e < n * 0.02 ** 2:  # silence
            continue
        total += 1
        spec = np.fft.rfft(f, 2 * n)
        ac = np.fft.irfft(spec * np.conj(spec))[: n]
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / (ac[0] + 1e-12) > 0.45:
            f0.append(sr / k)
            periodic += 1
    return np.array(f0), (periodic / total if total else 0.0)


def measure(clip: AudioClip) -> dict:
    """Return speech duration, pitch level and range, loudness, brightness, voicing."""
    if np is None:
        raise RuntimeError("numpy is not installed")
    sr = clip.sample_rate
    x = _speech_span(np.asarray(clip.samples, dtype=np.float64) / 32768.0, sr)
    if len(x) < sr * 0.1:
        return {"speech_s": round(len(x) / sr, 2)}
    f0, voiced_ratio = _f0_track(x, sr)
    active = x[np.abs(x) > 0.01]
    spec = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    out = {
        "speech_s": round(len(x) / sr, 2),
        "loudness_db": round(float(20 * np.log10(np.sqrt(np.mean(active ** 2)) + 1e-12)), 1),
        "brightness_hz": round(float(np.sum(freqs * spec) / (np.sum(spec) + 1e-12))),
        "voiced_ratio": round(float(voiced_ratio), 2),
    }
    if len(f0) >= 5:
        out["pitch_hz"] = round(float(np.median(f0)))
        out["pitch_range_st"] = round(float(12 * np.log2(np.percentile(f0, 95) / np.percentile(f0, 5))), 1)
    return out
