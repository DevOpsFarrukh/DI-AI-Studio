"""Post-processing audio effects for emotions the speech engine cannot express itself.

These are SIMULATIONS. They shape the sound with ffmpeg filters (trembling, punch,
breathiness, darkness, room echo); they do not make the engine feel anything. Any
emotion that uses them is labelled "simulated" in the API and UI.

Each effect maps an amount in 0..1 (already scaled by intensity) to an ffmpeg
filter chain. Add an effect by adding a builder to EFFECTS.
"""
from __future__ import annotations

from typing import Callable

from .models import EffectSpec


def _tremble(a: float) -> str:
    # pitch wobble + amplitude wobble, like a shaky voice
    return f"vibrato=f=6:d={0.08 + 0.35 * a:.3f},tremolo=f=5:d={0.05 + 0.3 * a:.3f}"


def _punch(a: float) -> str:
    # compressed, forward, slightly harsher: angry / urgent projection
    return (f"acompressor=threshold=0.06:ratio={1 + 5 * a:.2f}:attack=4:release=60:makeup={1 + 1.5 * a:.2f},"
            f"equalizer=f=2800:t=q:w=1.2:g={5 * a:.1f}")


def _whisper(a: float) -> str:
    # ffmpeg's documented "whisperization": randomise phase, keep the spectrum.
    # Blended with the dry voice by `a` (1.0 = fully breathy).
    fx = ("afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)'"
          ":imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)':win_size=256:overlap=0.75,highpass=f=300")
    return f"asplit=2[dry][wet];[wet]{fx}[w];[dry][w]amix=inputs=2:weights='{1 - a:.2f} {a:.2f}':normalize=0"


def _darken(a: float) -> str:
    # duller, more subdued tone (sad, tired)
    return f"lowpass=f={int(9000 - 5500 * a)}"


def _brighten(a: float) -> str:
    return f"equalizer=f=3500:t=q:w=1.5:g={4 * a:.1f}"


def _reverb(a: float) -> str:
    # short room / hall echo for cinematic and dramatic narration
    return f"aecho=0.85:0.8:55|95:{0.35 * a:.3f}|{0.22 * a:.3f}"


_NORMALISE = "aformat=sample_fmts=s16:channel_layouts=mono"

EFFECTS: dict[str, Callable[[float], str]] = {
    "tremble": _tremble,
    "punch": _punch,
    "whisper": _whisper,
    "darken": _darken,
    "brighten": _brighten,
    "reverb": _reverb,
}


def validate(spec: EffectSpec) -> None:
    if spec.type not in EFFECTS:
        raise ValueError(f"unknown effect '{spec.type}'. Known: {', '.join(sorted(EFFECTS))}")
    if not 0.0 <= spec.amount <= 1.0:
        raise ValueError(f"effect '{spec.type}': amount must be between 0 and 1")


def filter_graph(effects: tuple[EffectSpec, ...] | list[EffectSpec]) -> str | None:
    """Chain effects into one ffmpeg -filter_complex graph, or None if nothing to do."""
    stages = [EFFECTS[e.type](e.amount) for e in effects if e.amount > 0.001]
    if not stages:
        return None
    graph, src = [], "0:a"
    for i, stage in enumerate(stages):
        if "[dry]" in stage:  # multi-branch stage: rename internal labels uniquely
            stage = stage.replace("[dry]", f"[d{i}]").replace("[wet]", f"[w{i}]").replace("[w]", f"[x{i}]")
        last = i == len(stages) - 1
        # Convert to 16-bit between stages. With ffmpeg 4.4, vibrato can emit invalid
        # samples that silenced everything after it (tremble -> punch, tremble -> whisper);
        # the conversion clears them. Tested on all 30 ordered effect pairs.
        # The final stage stays unlabelled so ffmpeg maps it to the output automatically.
        graph.append(f"[{src}]{stage}" + ("" if last else f",{_NORMALISE}[s{i}]"))
        src = f"s{i}"
    return ";".join(graph)
