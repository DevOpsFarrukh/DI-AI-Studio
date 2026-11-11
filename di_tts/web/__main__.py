"""Start the web server. All settings come from environment variables (12-factor),
so the same image runs on a laptop, EC2 or ECS without code changes.

    DI_TTS_HOST           bind address (default 127.0.0.1; the container sets 0.0.0.0)
    DI_TTS_PORT           port (default 8000)
    DI_TTS_KEEPALIVE_S    idle keep-alive seconds (default 65). Must be longer than the
                          load balancer's idle timeout (AWS ALB default: 60 s); otherwise
                          the ALB reuses connections the server already closed -> random 502s.
    FORWARDED_ALLOW_IPS   proxies trusted for X-Forwarded-* headers (default 127.0.0.1;
                          the container sets "*" because it only runs behind a proxy/ALB)
    DI_TTS_LOG_LEVEL      uvicorn log level (default info)
"""
import os

import uvicorn

uvicorn.run(
    "di_tts.web.app:app",
    host=os.environ.get("DI_TTS_HOST", "127.0.0.1"),
    port=int(os.environ.get("DI_TTS_PORT", 8000)),
    proxy_headers=True,
    forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
    timeout_keep_alive=int(os.environ.get("DI_TTS_KEEPALIVE_S", 65)),
    log_level=os.environ.get("DI_TTS_LOG_LEVEL", "info"),
)
