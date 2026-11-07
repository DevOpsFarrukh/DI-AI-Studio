"""Command-line interface.

    python -m di_tts render examples/demo_script.txt -o out/demo.wav
    python -m di_tts render script.txt --backend espeak   # force one engine for all voices
    python -m di_tts list
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .backends import BackendUnavailable, registered_backends
from .engine import TTSEngine
from .models import ScriptLine

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="di_tts", description="Multi-voice text-to-speech")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="dir with voices/ and emotions.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("render", help="render a script to WAV")
    r.add_argument("script", type=Path)
    r.add_argument("-o", "--output", type=Path, default=Path("out/output.wav"))
    r.add_argument("--backend", choices=registered_backends(), help="override every voice's backend")
    r.add_argument("--sample-rate", type=int, default=22050)

    s = sub.add_parser("say", help="speak a piece of text with one voice")
    s.add_argument("text", help="text to speak (use '-' to read from stdin)")
    s.add_argument("-v", "--voice", default="narrator")
    s.add_argument("-e", "--emotion", default="neutral")
    s.add_argument("-i", "--intensity", type=float, default=1.0)
    s.add_argument("-o", "--output", type=Path, default=Path("out/say.wav"))
    s.add_argument("--backend", choices=registered_backends())

    sub.add_parser("list", help="list voices, emotions and backends")
    args = p.parse_args(argv)

    if args.cmd == "say":
        text = sys.stdin.read() if args.text == "-" else args.text
        eng = TTSEngine.from_config_dir(args.config, backend_override=args.backend)
        line = ScriptLine(args.voice.lower(), " ".join(text.split()), args.emotion.lower(), args.intensity, 1)
        try:
            result = eng.render_lines([line])
        except (KeyError, BackendUnavailable) as e:
            print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
            return 2
        wav, _ = result.save(args.output)
        for w in result.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"wrote {wav} ({result.audio.duration_s:.1f}s)")
        return 0

    if args.cmd == "list":
        eng = TTSEngine.from_config_dir(args.config)
        print("Voices:")
        for v in eng.voices.all():
            print(f"  {v.id:<12} backend={v.backend:<8} aliases={', '.join(v.aliases) or '-'}")
        print("Emotions:", ", ".join(eng.emotions.names()))
        print("Backends:", ", ".join(registered_backends()))
        return 0

    eng = TTSEngine.from_config_dir(args.config, sample_rate=args.sample_rate, backend_override=args.backend)
    try:
        result = eng.render(args.script.read_text(encoding="utf-8"))
    except (KeyError, BackendUnavailable) as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        return 2
    wav, cue = result.save(args.output)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    print(f"wrote {wav} ({result.audio.duration_s:.1f}s, {len(result.timeline)} lines) and {cue}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
