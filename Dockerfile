FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DI_TTS_HOST=0.0.0.0 DI_TTS_PORT=8000 DI_TTS_OUTPUT=/data/out

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Voice models are baked into the image so the container works offline.
RUN python -m piper.download_voices --data-dir /app/models \
      en_US-lessac-medium en_US-ryan-medium en_US-amy-medium

COPY di_tts ./di_tts
COPY config ./config

RUN useradd --create-home app && mkdir -p /data/out && chown -R app /data
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["python", "-m", "di_tts.web"]
