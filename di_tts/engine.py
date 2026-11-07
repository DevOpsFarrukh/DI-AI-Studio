"""The rendering pipeline.

    script text
      -> parse_script()          ScriptLine[]         (who, what, emotion)
      -> resolve()               Utterance[]          (voice + combined prosody)
      -> chunk_text()            Utterance[] (small)  (long-script safety)
      -> backend.synthesize()    AudioClip per chunk
      -> post-process            fallbacks for unsupported prosody, edge fades
      -> assemble                one WAV + a JSON timeline (cue sheet)
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import backends as _backends  # noqa: F401  ensures registration
from .audio import AudioClip, concat, fade_edges, pitch_shift
from .backends.base import BackendUnavailable, TTSBackend, create_backend
from .models import ScriptLine, Utterance, VoiceProfile
from .registry import EmotionRegistry, VoiceRegistry
from .script import chunk_text, parse_script


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
        prosody = voice.prosody.combine(emotion.prosody.scaled(line.intensity))
        return Utterance(line.text, voice, prosody, line)

    def render_lines(self, lines: list[ScriptLine]) -> RenderResult:
        # Resolve everything first so unknown speakers/emotions fail before any synthesis.
        utterances = [self.resolve(l) for l in lines]
        clips: list[AudioClip] = []
        timeline: list[TimelineEntry] = []
        warnings: list[str] = []
        t = 0.0
        for utt in utterances:
            backend = self._backend_for(utt.voice, warnings)
            unsupported = {k for k in ("pitch_semitones", "volume_db")
                           if getattr(utt.prosody, k) != 0} - backend.supports
            start = t
            for chunk in chunk_text(utt.text, self.max_chunk_chars):
                clip = backend.synthesize(utt.with_text(chunk))
                if "volume_db" in unsupported:
                    clip = clip.gain(utt.prosody.volume_db)
                if "pitch_semitones" in unsupported:
                    try:
                        clip = pitch_shift(clip, utt.prosody.pitch_semitones)
                    except RuntimeError as e:
                        unsupported.discard("pitch_semitones")
                        warnings.append(f"line {utt.source.line_no}: pitch not applied ({e})")
                clip = fade_edges(clip.resample(self.sample_rate))
                clips.append(clip)
                t += clip.duration_s
                gap = AudioClip.silence(120, self.sample_rate)  # between chunks of one line
                clips.append(gap)
                t += gap.duration_s
            clips.pop(); t -= 0.12  # replace trailing chunk gap with the line pause
            pause = AudioClip.silence(utt.prosody.pause_after_ms, self.sample_rate)
            timeline.append(TimelineEntry(utt.source.line_no, utt.source.speaker, utt.voice.id,
                                          utt.source.emotion, utt.text, round(start, 3), round(t, 3)))
            clips.append(pause)
            t += pause.duration_s
        return RenderResult(concat(clips, self.sample_rate), timeline, warnings)

    def render(self, script: str) -> RenderResult:
        return self.render_lines(parse_script(script))
