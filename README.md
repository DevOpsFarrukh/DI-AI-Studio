# DI-AI-Studio — Multi-voice TTS

A small, modular text-to-speech pipeline. It turns a script with several characters and basic emotions into one WAV file and a timeline. It is a clean-room design: no third-party or proprietary code is copied. Speech engines are pluggable adapters around open-source tools.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m piper.download_voices --data-dir models en_US-lessac-medium en_US-ryan-medium en_US-amy-medium

.venv/bin/python -m di_tts say "Hello there!" -v alice -e happy -o out/hello.wav   # one line of text
.venv/bin/python -m di_tts render examples/demo_script.txt -o out/demo.wav          # a multi-character script
.venv/bin/python -m di_tts list
paplay out/hello.wav                                                                  # listen (or aplay / ffplay)
python3 -m unittest                     # tests use the dependency-free "tone" backend
```

Without Piper or the models, each voice falls back to the built-in `tone` backend. It renders placeholder tones, not speech, so you can still test the pipeline, and the CLI prints a warning.

## Web app

```bash
.venv/bin/python -m di_tts.web            # then open http://127.0.0.1:8000
```

- **Quick speak:** type text, pick a voice, an emotion and an intensity, then play or download the WAV.
- **Script studio:** write a multi-character script and render it. Click a line in the timeline to jump to it in the audio.
- **API:**
  - `GET /api/voices`
  - `POST /api/say {text, voice, emotion, intensity}`
  - `POST /api/render {script}`
  - `GET /api/health`
  - Interactive docs at `/docs`.

To run it on your own domain with HTTPS, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). It covers Docker, Caddy and the API key.

## Script format

```text
# comments are ignored
NARRATOR: The station was quiet.
ALICE (happy): It is the answer!          # emotion
BOB (sad:0.6): I was hoping it was nothing.   # emotion with intensity 0..1
  An indented line without "NAME:" continues the previous speaker.
```

## Architecture

```
script.txt ─▶ Parser ─▶ Resolver ─────────────▶ Chunker ─▶ Backend ─────────▶ Post-process ─▶ Assembler ─▶ out.wav
             (lines)   voice + emotion → Prosody (sentences) (tone│piper│espeak) (gain, pitch,   (pauses,     + .timeline.json
                        ▲            ▲                         ▲                 fades, resample) timeline)
                  VoiceRegistry  EmotionRegistry        Backend registry
                  config/voices/ config/emotions.json   register_backend()
```

| Component | File | Role |
|---|---|---|
| Data model | `di_tts/models.py` | `Prosody` (rate, pitch, volume, pause), `VoiceProfile`, `Emotion`, `ScriptLine`, `Utterance` |
| Parser / chunker | `di_tts/script.py` | Script → `ScriptLine`s; splits long text at sentence boundaries |
| Registries | `di_tts/registry.py` | Load voices and emotions from JSON |
| Backend contract | `di_tts/backends/base.py` | `TTSBackend.synthesize(Utterance) -> AudioClip` and the `supports` set |
| Backends | `di_tts/backends/` | `tone` (stdlib test backend), `piper` (neural, MIT), `espeak` (formant, GPL, run as a subprocess) |
| Engine | `di_tts/engine.py` | Resolve → chunk → synthesize → fallbacks → assemble |
| Audio | `di_tts/audio.py` | PCM16 clip, WAV I/O, resample, gain, ffmpeg pitch shift |
| CLI | `di_tts/cli.py` | `render`, `list` |

**Prosody layering:** `final = voice.prosody ⊕ emotion.prosody × intensity`. Rates multiply, and pitch and volume offsets add. A backend renders the controls it lists in `supports`. For the rest, the engine falls back to gain for volume and ffmpeg for pitch.

## Extending

- **New voice:** add `config/voices/<id>.json`. No code change is needed.
- **New emotion:** add an entry to `config/emotions.json`, for example `"angry": {"prosody": {"rate": 1.15, "volume_db": 3}}`.
- **New engine** (Coqui XTTS, a cloud API, an in-house model): subclass `TTSBackend` and call `register_backend("name", Factory)`. Then set `"backend": "name"` in a voice file.
- **Longer scripts:** chunking already bounds the size of each request. Next steps are listed below.

## Roadmap

1. Emotion-aware backends, for example models with style or emotion embeddings, where the `Emotion` maps to a native style ID instead of only prosody.
2. A synthesis cache keyed by (voice, prosody, text) and parallel rendering, for long scripts.
3. Richer input: an SSML subset or JSON scripts, plus a pronunciation lexicon.
4. Streaming output and an HTTP API.

## Licensing notes

- **This repo's code** is clean-room and owned by the project.
- **piper-tts 1.8** is **GPL-3.0-or-later**. That is fine for internal use and for running a hosted service. Obligations apply only if you distribute the software, for example by shipping the Docker image or app to others.
- **Voice models** are downloaded separately into `models/`, which is git-ignored. The three demo voices are **not cleared for business use**:
  - `lessac`: Blizzard 2013 data, research-only.
  - `ryan`: CC BY-NC-SA 4.0, non-commercial.
  - `amy`: license not stated clearly.

  Replace them with permissively licensed voices before any company or production use.
- **espeak-ng** is GPL-3.0 and is called only as an external program.
