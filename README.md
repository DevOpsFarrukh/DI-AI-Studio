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
- **Script studio:** write a multi-character script and render it. Click a line in the timeline to jump to it in the audio. Use **Insert emotion** to tag the line under the cursor.
- **Emotion Lab** (`/#lab`): the same sentence and voice in several emotions, or one emotion at intensities 0.2/0.5/0.8/1.0. Every version is playable and measured (length, pitch, loudness, brightness, voicing), compared with neutral.
- **API:**
  - `GET /api/voices` (also `emotion_details`, each with how it is produced)
  - `POST /api/say {text, voice, emotion, intensity, analyze}`
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

## Emotions

15 emotions, all defined in `config/emotions.json`:

| Group | Emotions |
|---|---|
| Basic | neutral, happy, excited, sad, angry, calm, fearful |
| Advanced | serious, curious, nervous, dramatic, urgent |
| Delivery styles | whisper, cinematic (narration), news (presenter) |

**Piper has no emotion control,** so each emotion is built from things Piper and ffmpeg can actually change:

- **Speed, pitch and volume.** Volume is applied after every piece is set to the same loudness, so levels stay consistent.
- **Pauses.** Real silence between sentences and between phrases. Piper ignores "...", so ellipses alone do nothing.
- **Rhythm steadiness.** Piper's `noise_w` setting.
- **Punctuation.** Ending with "!" made Piper about 11% faster and widened its melody by about 50%; sad and calm turn "!" into ".".
- **Audio effects:** tremble, punch, whisper, darken, brighten and reverb.

Every emotion is labelled **baseline**, **approximated** (no effects) or **simulated** (uses audio effects) in the API and UI. Simulated means an effect, not genuine emotional speech.

**Not possible with Piper:** crying, laughing, sarcasm and true shouting. A script that asks for them fails with a list of the available emotions.

**Intensity** (0 to 1) scales every setting, effect and pause from neutral. Punctuation changes switch on at a threshold, for example happy becomes exclamatory at 0.5.

**Measured,** not assumed:
- On 2026-11-07, all 105 pairs of emotions differed on at least one measured feature, on all three voices, in 3 runs each.
- `tests/test_emotions.py` checks the main differences on real audio, for example that sad is more than 30% longer than neutral and that whisper is mostly unvoiced.
- Piper's timing varies by about ±5% from run to run.
- Measurable differences do not guarantee a listener hears the intended emotion. Use the Emotion Lab to judge by ear.

```bash
.venv/bin/python -m di_tts lab -v alice                                 # all emotions, measured
.venv/bin/python -m di_tts lab -v bob -e sad,happy -i 0.2,0.5,0.8,1.0   # intensity sweep
.venv/bin/python -m di_tts render examples/emotions_demo.txt -o out/emotions_demo.wav
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
| Data model | `di_tts/models.py` | `Prosody` (rate, pitch, volume, pauses, rhythm), `TextStyle`, `EffectSpec`, `VoiceProfile`, `Emotion`, `ScriptLine`, `Utterance` |
| Parser / chunker | `di_tts/script.py` | Script → `ScriptLine`s; sentence chunking, clause splitting, punctuation shaping |
| Effects | `di_tts/effects.py` | Emotion audio effects as ffmpeg filter graphs |
| Analysis | `di_tts/analysis.py` | Acoustic measurements for the Emotion Lab and audio tests (needs numpy) |
| Registries | `di_tts/registry.py` | Load voices and emotions from JSON |
| Backend contract | `di_tts/backends/base.py` | `TTSBackend.synthesize(Utterance) -> AudioClip` and the `supports` set |
| Backends | `di_tts/backends/` | `tone` (stdlib test backend), `piper` (neural, MIT), `espeak` (formant, GPL, run as a subprocess) |
| Engine | `di_tts/engine.py` | Resolve → chunk → synthesize → fallbacks → assemble |
| Audio | `di_tts/audio.py` | PCM16 clip, WAV I/O, resample, gain, ffmpeg pitch shift |
| CLI | `di_tts/cli.py` | `say`, `render`, `lab`, `list` |

**Prosody layering:**
- `final = voice.prosody ⊕ emotion.prosody × intensity`.
- Rates and pause scales multiply; pitch, volume and clause pauses add.
- A backend renders the controls it lists in `supports`. For pitch, the engine falls back to ffmpeg.
- The engine always applies volume itself, after normalizing each piece to -20 dBFS and applying the effects.

## Extending

- **New voice:** add `config/voices/<id>.json`. No code change is needed.
- **New emotion:** add an entry to `config/emotions.json`. For example:

  ```json
  "tired": {"category": "advanced", "prosody": {"rate": 0.85, "volume_db": -2, "clause_pause_ms": 200},
            "text": {"soften_at": 0.3}, "effects": [{"type": "darken", "amount": 0.4}]}
  ```

  Then check it in the Emotion Lab.
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
