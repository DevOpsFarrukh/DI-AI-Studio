"""Script parsing and text chunking.

Script format (plain text, one utterance per line):

    # comment lines and blank lines are ignored
    NARRATOR: Once upon a time...
    ALICE (happy): Hello there!
    BOB (sad:0.5): I suppose so.      # emotion with intensity 0..1
    Continuation lines without a "SPEAKER:" prefix belong to the previous speaker.

The parser is deliberately small; a richer format (SSML, JSON, screenplay)
can be added as another parser that yields the same `ScriptLine` objects.
"""
from __future__ import annotations

import re
from typing import Iterable, Iterator

from .models import ScriptLine

_LINE_RE = re.compile(
    r"""^\s*
    (?P<speaker>[A-Za-z][\w .'-]{0,40}?)          # speaker name
    \s*(?:\(\s*(?P<emotion>[a-zA-Z_]+)\s*(?::\s*(?P<intensity>[0-9.]+))?\s*\))?
    \s*:\s*(?P<text>.+?)\s*$""",
    re.VERBOSE,
)


class ScriptError(ValueError):
    pass


def parse_script(source: str | Iterable[str], default_speaker: str = "narrator") -> list[ScriptLine]:
    lines = source.splitlines() if isinstance(source, str) else list(source)
    out: list[ScriptLine] = []
    for no, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _LINE_RE.match(stripped)
        if m:
            intensity = float(m["intensity"]) if m["intensity"] else 1.0
            if not 0.0 <= intensity <= 1.0:
                raise ScriptError(f"line {no}: intensity must be between 0 and 1")
            out.append(
                ScriptLine(
                    speaker=m["speaker"].strip().lower(),
                    text=m["text"],
                    emotion=(m["emotion"] or "neutral").lower(),
                    intensity=intensity,
                    line_no=no,
                )
            )
        elif out:  # continuation of previous speaker
            prev = out[-1]
            out[-1] = ScriptLine(prev.speaker, f"{prev.text} {stripped}", prev.emotion, prev.intensity, prev.line_no)
        else:
            out.append(ScriptLine(default_speaker, stripped, line_no=no))
    return out


_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")


def chunk_text(text: str, max_chars: int = 240) -> Iterator[str]:
    """Split long text at sentence boundaries, then at commas/spaces if still too long.

    Keeps backend requests small, which bounds memory and latency for long scripts.
    """
    for sentence in _SENTENCE_RE.split(text.strip()):
        while len(sentence) > max_chars:
            cut = max(sentence.rfind(",", 0, max_chars), sentence.rfind(" ", 0, max_chars))
            cut = cut if cut > 0 else max_chars
            yield sentence[: cut + 1].strip()
            sentence = sentence[cut + 1 :].strip()
        if sentence:
            yield sentence
