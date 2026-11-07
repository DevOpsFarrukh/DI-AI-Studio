# Deployment: from localhost to your own domain

## Stage 1: Local (now)

```bash
.venv/bin/python -m di_tts.web          # open http://127.0.0.1:8000
```
The server listens only on `127.0.0.1`, so other machines can't reach it.

To try it from a phone or another PC on the same Wi-Fi, run `DI_TTS_HOST=0.0.0.0 .venv/bin/python -m di_tts.web` and open `http://<this-PC-IP>:8000`.

## Stage 2: Container (portable)

```bash
docker build -t di-tts .
docker run --rm -p 8000:8000 di-tts      # http://127.0.0.1:8000
```
The image includes ffmpeg and the three Piper voice models, so it runs offline.

## Stage 3: Public domain with HTTPS

1. **Server:** rent a Linux VPS with at least 2 vCPU and 4 GB RAM (Hetzner, DigitalOcean, AWS Lightsail, etc.) and install Docker.
2. **DNS:** at your domain registrar, add an `A` record such as `tts.yourdomain.com` that points to the VPS IP.
3. **Firewall:** open ports 80 and 443 only.
4. **Deploy:**
   ```bash
   git clone https://github.com/DevOpsFarrukh/DI-AI-Studio && cd DI-AI-Studio
   cp deploy/.env.example deploy/.env      # set DOMAIN and a long random DI_TTS_API_KEY
   docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
   ```
   Caddy gets a Let's Encrypt certificate automatically and then serves `https://tts.yourdomain.com`.
5. **Use it:** open the site, go to **Settings**, and paste the API key.

## Before going public: checklist

| Item | Status |
|---|---|
| HTTPS | Caddy, automatic |
| Access control | Shared API key (`DI_TTS_API_KEY`). Replace with real user accounts later (OAuth or a login page) |
| Abuse limits | Character limit per request, 1 MB body limit. **To do:** per-IP rate limit (Caddy plugin or app middleware) |
| Disk usage | Only the last `DI_TTS_KEEP_FILES` renders are kept (default 200) |
| Concurrency | One render at a time per container. Scale by adding a job queue (Redis + workers) when needed |
| Voice licensing | Check each Piper voice's model card before commercial use |
| Monitoring | `/api/health` endpoint. Add uptime monitoring (UptimeRobot, etc.) |

## Next architecture steps

```
Browser ─HTTPS─▶ Caddy ─▶ FastAPI (API + static UI) ─▶ job queue ─▶ TTS workers (CPU/GPU)
                                   │                                 │
                                   └──── Postgres (users, projects) ─┘── object storage (S3) for audio
```
Add these as needed: user accounts and saved projects (Postgres), a queue for long scripts, GPU workers for heavier voice models, and S3 storage for audio.
