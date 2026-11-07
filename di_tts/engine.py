"""The rendering pipeline.

    script text
      -> parse_script()          ScriptLine[]         (who, what, emotion)
      -> resolve()               Utterance[]          (voice + emotion prosody scaled by
                                                       intensity, punctuation shaping, effects)
      -> pieces                  sentences (chunk_text), optionally split into clauses,
                                 each followed by a pause
      -> backend.synthesize()    AudioClip per piece
      -> post-process            fallbacks for unsupported prosody, emotion effects
                                 (loudness-matched), edge fades
      -> assemble                one WAV + a JSON timeline (cue sheet)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import backends as _backends  # noqa: F401  ensures registration
from .audio import AudioClip, concat, fade_edges, ffmpeg_available, ffmpeg_filter, pitch_shift
from .backends.base import BackendUnavailable, TTSBackend, create_backend
from .effects import filter_graph
from .models import EffectSpec, ScriptLine, Utterance, VoiceProfile
from .registry import EmotionRegistry, VoiceRegistry
from .script import chunk_text, parse_script, shape_text, split_clauses

BASE_SENTENCE_GAP_MS = 120
# Speech loudness every piece is set to before the emotion's volume change (active RMS).
# -20 dBFS leaves about 6 dB of room for the loudest emotions before peaks are clipped.
REFERENCE_LOUDNESS_DBFS = -20.0

# How an emotion is produced, reported to users so nothing is passed off as more than it is.
REALIZATION_NOTES = {
    "baseline": "The voice's normal delivery.",
    "native": "The speech engine itself renders this emotion.",
    "approximated": "The engine has no emotion control. Delivered through speed, pitch, volume, "
                    "pauses, rhythm and punctuation.",
    "simulated": "Approximated delivery plus audio effects ({effects}). An effect, not genuine "
                 "emotional speech.",
    "placeholder": "Test backend: tones, not speech.",
}


@dataclass
class TimelineEntry:
    line_no: int
    speaker: str
    voice: str
    emotion: str
    text: str
    start_s: float
    end_s: float


@dataclass
class RenderResult:
    audio: AudioClip
    timeline: list[TimelineEntry]
    warnings: list[str] = field(default_factory=list)

    def save(self, wav_path: str | Path) -> tuple[Path, Path]:
        wav = self.audio.write_wav(wav_path)
        cue = wav.with_suffix(".timeline.json")
        cue.write_text(json.dumps({"warnings": self.warnings,
                                   "entries": [asdict(e) for e in self.timeline]}, indent=2))
        return wav, cue


class TTSEngine:
    def __init__(self, voices: VoiceRegistry, emotions: EmotionRegistry,
                 sample_rate: int = 22050, max_chunk_chars: int = 240,
                 backend_override: str | None = None) -> None:
        self.voices, self.emotions = voices, emotions
        self.sample_rate, self.max_chunk_chars = sample_rate, max_chunk_chars
        self.backend_override = backend_override
        self._backends: dict[str, TTSBackend] = {}

    @classmethod
    def from_config_dir(cls, root: str | Path, **kw) -> "TTSEngine":
        root = Path(root)
        return cls(VoiceRegistry.from_dir(root / "voices"),
                   EmotionRegistry.from_file(root / "emotions.json"), **kw)

    def _get_backend(self, name: str) -> TTSBackend:
        if name not in self._backends:
            b = create_backend(name)
            b.check()  # raises BackendUnavailable
            self._backends[name] = b
        return self._backends[name]

    def _backend_for(self, voice: VoiceProfile, warnings: list[str]) -> TTSBackend:
        if self.backend_override:
            return self._get_backend(self.backend_override)
        try:
            return self._get_backend(voice.backend)
        except BackendUnavailable as e:
            if not voice.fallback_backend:
                raise
            msg = f"voice '{voice.id}': {e}; using fallback '{voice.fallback_backend}'"
            if msg not in warnings:
                warnings.append(msg)
            return self._get_backend(voice.fallback_backend)

    def resolve(self, line: ScriptLine) -> Utterance:
        voice = self.voices.get(line.speaker)
        emotion = self.emotions.get(line.emotion)
        i = line.intensity
        prosody = voice.prosody.combine(emotion.prosody.scaled(i))
        effects = tuple(EffectSpec(e.type, round(min(1.0, e.amount * i), 3))
                        for e in emotion.effects if e.amount * i > 0.001)
        return Utterance(shape_text(line.text, emotion.text, i), voice, prosody, line, effects)

    def realization(self, emotion_name: str, backend_name: str) -> dict:
        """How `emotion_name` is produced on `backend_name`: level, notes, availability."""
        emotion = self.emotions.get(emotion_name)
        backend = create_backend(backend_name)
        fx = [e.type for e in emotion.effects]
        if backend.name == "tone":
            level = "placeholder"
        elif emotion.name == "neutral":
            level = "baseline"
        elif "emotion" in backend.supports:
            level = "native"
        elif fx:
            level = "simulated"
        else:
            level = "approximated"
        available = not fx or ffmpeg_available()
        notes = REALIZATION_NOTES[level].format(effects=", ".join(fx))
        if not available:
            notes += " ffmpeg is not installed, so the effects cannot be applied on this machine."
        return {"level": level, "notes": notes, "effects": fx, "available": available}

    def _pieces(self, utt: Utterance) -> list[tuple[str, int]]:
        """Split an utterance into (text, pause-after-ms) pieces: sentences, then clauses."""
        sentence_gap = int(BASE_SENTENCE_GAP_MS * utt.prosody.pause_scale)
        clause_gap = utt.prosody.clause_pause_ms
        pieces: list[tuple[str, int]] = []
        for sentence in chunk_text(utt.text, self.max_chunk_chars):
            # clause pauses of 150 ms or more (sad, dramatic, serious, ...) also split
            # comma-less sentences once, so the pause is heard on short lines too
            clauses = split_clauses(sentence, phrase_split=clause_gap >= 150) if clause_gap > 0 else [sentence]
            pieces += [(c, clause_gap) for c in clauses[:-1]] + [(clauses[-1], sentence_gap)]
        return pieces

    def _synth_piece(self, backend: TTSBackend, utt: Utterance, unsupported: set[str],
                     warnings: list[str]) -> AudioClip:
        # Every piece is first set to one reference loudness, because engines differ
        # (Piper peak-normalises each call, so short clauses came out ~3 dB louder than
        # whole sentences). Volume changes are then applied by the engine, identically
        # for every backend, after effects.
        clip = backend.synthesize(utt).normalize(REFERENCE_LOUDNESS_DBFS)
        if "pitch_semitones" in unsupported:
            try:
                clip = pitch_shift(clip, utt.prosody.pitch_semitones)
            except RuntimeError as e:
                unsupported.discard("pitch_semitones")
                warnings.append(f"line {utt.source.line_no}: pitch not applied ({e})")
        graph = filter_graph(utt.effects) if "effects" not in unsupported else None
        if graph:
            try:
                clip = ffmpeg_filter(clip, graph, complex_graph=True).match_loudness(clip)
            except RuntimeError as e:
                unsupported.add("effects")
                warnings.append(f"line {utt.source.line_no}: emotion '{utt.source.emotion}' rendered "
                                f"without its audio effects ({e})")
        clip = clip.gain(utt.prosody.volume_db)
        return fade_edges(clip.resample(self.sample_rate))

    def render_lines(self, lines: list[ScriptLine]) -> RenderResult:
        # Resolve everything first so unknown speakers/emotions fail before any synthesis.
        utterances = [self.resolve(l) for l in lines]
        clips: list[AudioClip] = []
        timeline: list[TimelineEntry] = []
        warnings: list[str] = []
        t = 0.0
        for utt in utterances:
            pieces = self._pieces(utt)
            if not pieces:
                warnings.append(f"line {utt.source.line_no}: nothing to speak, skipped")
                continue
            backend = self._backend_for(utt.voice, warnings)
            unsupported = {"pitch_semitones"} - backend.supports if utt.prosody.pitch_semitones else set()
            start = t
            for k, (text, gap_ms) in enumerate(pieces):
                clip = self._synth_piece(backend, utt.with_text(text), unsupported, warnings)
                clips.append(clip)
                t += clip.duration_s
                if k < len(pieces) - 1:  # gaps between pieces; the line pause follows the last one
                    gap = AudioClip.silence(gap_ms, self.sample_rate)
                    clips.append(gap)
                    t += gap.duration_s
            timeline.append(TimelineEntry(utt.source.line_no, utt.source.speaker, utt.voice.id,
                                          utt.source.emotion, utt.source.text, round(start, 3), round(t, 3)))
            pause = AudioClip.silence(utt.prosody.pause_after_ms, self.sample_rate)
            clips.append(pause)
            t += pause.duration_s
        return RenderResult(concat(clips, self.sample_rate), timeline, warnings)

    def render(self, script: str) -> RenderResult:
        return self.render_lines(parse_script(script))
