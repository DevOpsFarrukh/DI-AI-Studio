import os

import uvicorn

uvicorn.run(
    "di_tts.web.app:app",
    host=os.environ.get("DI_TTS_HOST", "127.0.0.1"),
    port=int(os.environ.get("DI_TTS_PORT", 8000)),
    proxy_headers=True,  # correct client info behind nginx/Caddy later
)
