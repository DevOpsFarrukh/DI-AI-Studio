# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Offline, multi-voice text-to-speech platform (engine + CLI + web GUI) for in-house use. Python 3.10.

## Commands

Always use `.venv/bin/python`. With system `python3`, Piper is not importable and every voice silently falls back to the `tone` backend (beeps, not speech). Only a warning is printed.

```bash
# setup (once)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m piper.download_voices --data-dir models en_US-lessac-medium en_US-ryan-medium en_US-amy-medium

# tests (stdlib unittest; they force the `tone` backend, so no models are needed)
.venv/bin/python -m unittest                                   # all
.venv/bin/python -m unittest tests.test_pipeline               # one module
.venv/bin/python -m unittest tests.test_pipeline.EngineTests.test_sad_is_slower_than_neutral   # one test

# run
.venv/bin/python -m di_tts.web                                 # GUI + API on http://127.0.0.1:8000 (/docs for API)
.venv/bin/python -m di_tts say "Hello" -v alice -e happy -o out/x.wav
.venv/bin/python -m di_tts render examples/demo_script.txt -o out/demo.wav   # also writes out/demo.timeline.json
.venv/bin/python -m di_tts list
.venv/bin/python -m di_tts render script.txt --backend tone    # force one backend for all voices
.venv/bin/python -m di_tts lab -v alice -e sad,happy -i 0.2,1.0  # render + measure emotions (out/lab/)

docker build -t di-tts . && docker run --rm -p 8000:8000 di-tts
```

There is no linter or formatter configured.

## Architecture

The pipeline is in `di_tts/engine.py` (`TTSEngine.render_lines`):

```
script text → parse_script() → ScriptLine[] → resolve() → Utterance
      (voice prosody ⊕ emotion prosody scaled by intensity; shape_text() punctuation; effects scaled)
  → _pieces(): chunk_text() sentences, split_clauses() when clause_pause_ms > 0
  → per piece: backend.synthesize() → normalize to -20 dBFS → ffmpeg pitch fallback
      → emotion effects (loudness-matched) → gain(volume_db, soft limiter) → resample + fade
  → concat with sentence/clause/line pauses → WAV + timeline (timeline shows the original text)
```

Key points that span several files:

- **Backends** (`di_tts/backends/`) implement `TTSBackend.synthesize(Utterance) -> AudioClip` and register by name with `register_backend()`. Registration is a side effect of importing `di_tts.backends`, so new backend modules must be imported in `backends/__init__.py`.
- **Prosody controls:** each backend declares the controls it handles natively in `supports` (`rate`, `pitch_semitones`, `rhythm_variation`, and `emotion` for a future engine with native emotion control). Pitch falls back to ffmpeg; if ffmpeg is missing, it is skipped with a warning.
- **Volume:** backends must **not** apply volume. The engine normalizes every piece to `REFERENCE_LOUDNESS_DBFS`, because Piper peak-normalizes each call, which made short clauses about 3 dB louder. It then applies `volume_db` itself.
- **Prosody layering** (`models.Prosody`): `voice.prosody.combine(emotion.prosody.scaled(intensity))`.
  - Rates, `pause_scale` and `rhythm_variation` multiply; pitch, volume and `clause_pause_ms` add; `pause_after_ms` takes the max.
  - Intensity 0..1 interpolates every field from neutral, so intensity 0 equals neutral (this is tested).
  - Every new `Prosody` field must default to a no-op, so older config files keep working.
- **Emotions** (`config/emotions.json`, validated in `registry.py`). Each emotion has:
  - `label`
  - `category` (basic / advanced / style)
  - `prosody`
  - `text` (`exclaim_at` / `soften_at` thresholds; see `script.shape_text`)
  - `effects` (types from `effects.EFFECTS`; the amount is multiplied by intensity)
- **Honesty labels:** `engine.realization()` reports each emotion as baseline / approximated / simulated / native / placeholder. The API and UI show it. Never label something native unless the backend really controls emotion.
- **Effects** (`effects.py`) are ffmpeg `-filter_complex` graphs. Stages are joined with `aformat=s16`: ffmpeg 4.4's vibrato emitted invalid samples that silenced later stages. Keep that when adding effects, and re-run the all-pairs check.
- **Measuring audio:** `analysis.measure()` (numpy) backs `di_tts lab`, `/api/say {analyze: true}` and `tests/test_emotions.py::AudioTests`. Those tests skip without Piper, the models, numpy or ffmpeg. Piper's timing is random by about ±5%, so audio-test thresholds must stay well above that.
- **Voices and emotions are data, not code:**
  - Voices: `config/voices/*.json` (backend, `backend_options`, default prosody, aliases, `fallback_backend`).
  - Emotions: `config/emotions.json`.
  - A relative `backend_options.model` path is resolved against the voice JSON's folder (`registry.py`). Speaker names in scripts match a voice `id` or alias, case-insensitively.
  - The same `backend_options` dict holds keys for several backends (e.g. `model` for piper, `wpm`/`pitch` for espeak, `base_pitch_hz` for tone), so `--backend` overrides keep working.
- **Fail-fast resolution:** every line is resolved before any synthesis, so an unknown speaker or emotion raises `KeyError` up front. The web layer maps `KeyError` to 422 and `BackendUnavailable` to 503.
- **Piper backend** (`subprocess_backends.py`) uses the in-process `piper` API with a per-model cache when importable, and otherwise shells out to the `piper` CLI. Piper has no pitch control, so pitch goes through the ffmpeg fallback.
- **Script format** (`script.py`): `NAME (emotion:intensity): text`. `#` starts a comment. An indented or unprefixed line continues the previous speaker; an unprefixed first line goes to `narrator`.
- **Web layer** (`di_tts/web/app.py`):
  - Built once at import from `DI_TTS_*` environment variables: host, port, config, output, max chars, how many files to keep, optional `DI_TTS_API_KEY`.
  - A single module-level `engine`; renders are serialised with a lock.
  - Audio is served at `/api/audio/<16-hex>.wav` without auth, by unguessable ID.
  - The UI is one dependency-free file, `di_tts/web/static/index.html`.
  - Tests monkeypatch `web.engine.backend_override` and `web.API_KEY`.
- **Deployment:** `deploy/` holds docker-compose with Caddy for automatic HTTPS. See `docs/DEPLOYMENT.md`. The Docker image includes the voice models.

## Project rules

- **Clean room:** never copy third-party or proprietary code into this repo. External projects may be used only as high-level references.
- **Voice licensing:** only add voices whose model card and dataset license are verified to allow commercial use. Record the license in the voice JSON. The current demo voices (lessac, ryan, amy) are **not** cleared for commercial use. `piper-tts` 1.8 is GPL-3.0: fine internally, but distributing it triggers GPL obligations.
- **Git-ignored:** `models/`, `out/` and `.venv/` are never committed.
- **Pushing:** push over SSH (the remote is `git@github.com:DevOpsFarrukh/DI-AI-Studio.git`; the repo's local `core.sshCommand` selects the key). Do feature work on branches.
- **Progress notes:** the maintainer keeps progress, decisions, assumptions and open questions in a tracker outside the repo, at `~/.claude/trackers/di-ai-studio/` (start with `STATUS.md`). If it exists on this machine, read it and keep it updated.
