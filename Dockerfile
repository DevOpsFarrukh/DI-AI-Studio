# DI-AI-Studio TTS: production image (AWS ECS/Fargate, EC2, or any Docker host).
#
#   docker build -t di-tts .                      # runtime image (default target)
#   docker build --target test -t di-tts:test .   # same image + runs the full test suite
#   docker run --rm -p 8000:8000 -e DI_TTS_API_KEY=change-me di-tts
#
# AWS Fargate and most EC2 instances are x86_64: on an ARM machine (e.g. Apple Silicon)
# build with --platform linux/amd64.

# ---------- base: system packages, Python deps, voice models ----------
FROM python:3.10-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg: pitch changes and emotion effects. curl-free image; health checks use Python.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Voice models are baked in so the container is self-contained and works offline.
# NOTE: these three demo voices are NOT licensed for commercial use (see README).
RUN python -m piper.download_voices --data-dir /app/models \
      en_US-lessac-medium en_US-ryan-medium en_US-amy-medium

COPY di_tts ./di_tts
COPY config ./config

# ---------- test: run the whole suite (including real-audio tests) inside the image ----------
FROM base AS test
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY tests ./tests
COPY examples ./examples
RUN python -m unittest -v

# ---------- runtime ----------
FROM base AS runtime
LABEL org.opencontainers.image.title="di-ai-studio-tts" \
      org.opencontainers.image.description="Offline multi-voice text-to-speech with emotions (Piper)" \
      org.opencontainers.image.source="https://github.com/DevOpsFarrukh/DI-AI-Studio"

ENV DI_TTS_HOST=0.0.0.0 \
    DI_TTS_PORT=8000 \
    DI_TTS_OUTPUT=/data/out \
    DI_TTS_KEEPALIVE_S=65 \
    FORWARDED_ALLOW_IPS=*

RUN useradd --create-home --uid 10001 app && mkdir -p /data/out && chown -R app /data
USER app
EXPOSE 8000

# Readiness: /api/health returns 503 if a voice model or ffmpeg is missing.
# start-period covers model loading on small instances.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "di_tts.web"]
