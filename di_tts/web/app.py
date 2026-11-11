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

import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import analysis
from ..audio import ffmpeg_available
from ..backends import BackendUnavailable, create_backend
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

app = FastAPI(title="DI-AI-Studio TTS", version="0.3.0")
log = logging.getLogger("uvicorn.error")

if not API_KEY and os.environ.get("DI_TTS_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost"):
    log.warning("DI_TTS_API_KEY is not set and the server is reachable from the network: "
                "anyone who can reach it can use it. Set DI_TTS_API_KEY for any shared or cloud deployment.")


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "missing or invalid API key")


class SayRequest(BaseModel):
    text: str = Field(min_length=1)
    voice: str = "narrator"
    emotion: str = "neutral"
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    analyze: bool = False  # include acoustic measurements (Emotion Lab)


# Deliveries the current Piper-based setup cannot produce, shown in the UI so nobody expects them.
NOT_SUPPORTED = ["crying", "laughing", "sarcasm", "true shouting", "genuine whispering (only simulated)"]


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


def _default_backend() -> str:
    """Backend whose capabilities the UI describes: the override, else the most common voice backend."""
    if engine.backend_override:
        return engine.backend_override
    names = [v.backend for v in engine.voices.all()]
    return max(set(names), key=names.count) if names else "tone"


def _run(lines: list[ScriptLine], analyze: bool = False) -> dict:
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
    out = {
        "id": rid,
        "audio_url": f"/api/audio/{rid}.wav",
        "duration_s": round(result.audio.duration_s, 2),
        "render_s": round(time.perf_counter() - t0, 2),
        "warnings": result.warnings,
        "timeline": [e.__dict__ for e in result.timeline],
    }
    if analyze and analysis.available():
        out["metrics"] = analysis.measure(result.audio)
    return out


@app.get("/api/health")
def health():
    """Readiness check for Docker, ECS and load balancers.

    200 when every voice can actually be rendered (backend present, model file on
    disk) and ffmpeg is available for pitch and effects; 503 with the problems
    otherwise, so a broken image is never put into service.
    """
    problems = []
    for v in engine.voices.all():
        name = engine.backend_override or v.backend
        try:
            create_backend(name).check()
        except (BackendUnavailable, KeyError) as e:
            problems.append(f"voice '{v.id}': {e}")
            continue
        model = v.backend_options.get("model")
        if name == "piper" and (not model or not Path(model).exists()):
            problems.append(f"voice '{v.id}': model file missing: {model}")
    if not ffmpeg_available():
        problems.append("ffmpeg not installed: pitch changes and emotion effects unavailable")
    body = {"status": "ok" if not problems else "degraded", "voices": len(engine.voices.all()),
            "emotions": len(engine.emotions.names()), "auth": bool(API_KEY), "problems": problems}
    return JSONResponse(body, status_code=200 if not problems else 503)


@app.get("/api/voices", dependencies=[Depends(require_key)])
def voices() -> dict:
    backend = _default_backend()
    details = []
    for e in engine.emotions.all():
        r = engine.realization(e.name, backend)
        details.append({"name": e.name, "label": e.display, "category": e.category,
                        "description": e.description, "realization": r["level"],
                        "realization_notes": r["notes"], "available": r["available"]})
    return {
        "voices": [{"id": v.id, "name": v.display_name, "backend": v.backend} for v in engine.voices.all()],
        "emotions": engine.emotions.names(),  # kept for backward compatibility
        "emotion_details": details,
        "backend": backend,
        "not_supported": NOT_SUPPORTED,
        "analysis": analysis.available(),
        "max_chars": MAX_CHARS,
    }


@app.post("/api/say", dependencies=[Depends(require_key)])
def say(req: SayRequest) -> dict:
    text = " ".join(req.text.split())
    _check_len(text)
    return _run([ScriptLine(req.voice.lower(), text, req.emotion.lower(), req.intensity, 1)], analyze=req.analyze)


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
