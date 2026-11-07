"""Web API + GUI for the TTS engine.

Run locally:
    .venv/bin/python -m di_tts.web            # http://127.0.0.1:8000

Settings (environment variables, so the same code runs locally and in production):
    DI_TTS_HOST        bind address            (default 127.0.0.1; use 0.0.0.0 in a container)
    DI_TTS_PORT        port                    (default 8000)
    DI_TTS_CONFIG      voices/emotions dir     (default ./config)
    DI_TTS_OUTPUT      where WAVs are stored   (default ./out/web)
    DI_TTS_MAX_CHARS   max characters/request  (default 5000)
    DI_TTS_KEEP_FILES  how many renders to keep on disk (default 200)
    DI_TTS_API_KEY     if set, every /api/* call needs header "X-API-Key: <value>"
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..backends import BackendUnavailable
from ..engine import RenderResult, TTSEngine
from ..models import ScriptLine
from ..script import ScriptError, parse_script

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
CONFIG = Path(os.environ.get("DI_TTS_CONFIG", ROOT / "config"))
OUTPUT = Path(os.environ.get("DI_TTS_OUTPUT", ROOT / "out" / "web"))
MAX_CHARS = int(os.environ.get("DI_TTS_MAX_CHARS", 5000))
KEEP_FILES = int(os.environ.get("DI_TTS_KEEP_FILES", 200))
API_KEY = os.environ.get("DI_TTS_API_KEY") or None

OUTPUT.mkdir(parents=True, exist_ok=True)
engine = TTSEngine.from_config_dir(CONFIG)
# Model inference is CPU-bound and backends cache loaded models; serialise renders.
_render_lock = threading.Lock()

app = FastAPI(title="DI-AI-Studio TTS", version="0.2.0")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "missing or invalid API key")


class SayRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = "narrator"
    emotion: str = "neutral"
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)


class ScriptRequest(BaseModel):
    script: str = Field(min_length=1)


def _check_len(text: str) -> None:
    if len(text) > MAX_CHARS:
        raise HTTPException(413, f"text is {len(text)} characters; the limit is {MAX_CHARS}")


def _cleanup() -> None:
    files = sorted(OUTPUT.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for f in files[:-KEEP_FILES] if KEEP_FILES > 0 else []:
        f.unlink(missing_ok=True)
        f.with_suffix(".timeline.json").unlink(missing_ok=True)


def _run(lines: list[ScriptLine]) -> dict:
    t0 = time.perf_counter()
    try:
        with _render_lock:
            result: RenderResult = engine.render_lines(lines)
    except KeyError as e:  # unknown voice/emotion
        raise HTTPException(422, e.args[0] if e.args else str(e))
    except BackendUnavailable as e:
        raise HTTPException(503, str(e))
    rid = uuid.uuid4().hex[:16]
    result.save(OUTPUT / f"{rid}.wav")
    _cleanup()
    return {
        "id": rid,
        "audio_url": f"/api/audio/{rid}.wav",
        "duration_s": round(result.audio.duration_s, 2),
        "render_s": round(time.perf_counter() - t0, 2),
        "warnings": result.warnings,
        "timeline": [e.__dict__ for e in result.timeline],
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "voices": len(engine.voices.all()), "auth": bool(API_KEY)}


@app.get("/api/voices", dependencies=[Depends(require_key)])
def voices() -> dict:
    return {
        "voices": [{"id": v.id, "name": v.display_name, "backend": v.backend} for v in engine.voices.all()],
        "emotions": engine.emotions.names(),
        "max_chars": MAX_CHARS,
    }


@app.post("/api/say", dependencies=[Depends(require_key)])
def say(req: SayRequest) -> dict:
    text = " ".join(req.text.split())
    _check_len(text)
    return _run([ScriptLine(req.voice.lower(), text, req.emotion.lower(), req.intensity, 1)])


@app.post("/api/render", dependencies=[Depends(require_key)])
def render(req: ScriptRequest) -> dict:
    _check_len(req.script)
    try:
        lines = parse_script(req.script)
    except ScriptError as e:
        raise HTTPException(422, str(e))
    if not lines:
        raise HTTPException(422, "the script has no lines to speak")
    return _run(lines)


@app.get("/api/audio/{name}")
def audio(name: str) -> FileResponse:
    # Audio is public by unguessable id, so a plain <audio src> works without headers.
    if not (name.endswith(".wav") and len(name) == 20 and name[:16].isalnum()):
        raise HTTPException(404)
    path = OUTPUT / name
    if not path.exists():
        raise HTTPException(404, "audio expired or not found")
    return FileResponse(path, media_type="audio/wav")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
