"""
InfoDashboard — FastAPI application.

Endpoints:
  POST /api/generate          SSE stream: NLP request → deployed Streamlit dashboard
  GET  /api/dashboards        list running dashboard containers
  DELETE /api/dashboards/{id} stop and remove a dashboard container
  GET  /                      serve the frontend chat UI
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv(".env.local")   # load first; falls back to .env if not found
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger(__name__)

from orchestration.graph import generate
from orchestration.state import DBConfig
from tools.docker_manager import list_dashboards, stop_dashboard

_TOOLS_DIR = Path(__file__).resolve().parent / "tools"
_FRPC_EXE  = _TOOLS_DIR / ("frpc.exe" if os.name == "nt" else "frpc-linux")
_FRPC_INI  = _TOOLS_DIR / "frpc-visitor.ini"
_frpc_proc: subprocess.Popen | None = None


def _socks5_alive(host: str = "127.0.0.1", port: int = 1080) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def _start_frpc() -> None:
    global _frpc_proc
    if _socks5_alive():
        _log.info("frpc: SOCKS5 proxy already up on :1080")
        return
    if not _FRPC_EXE.exists():
        _log.warning("frpc: %s not found — skipping (DB calls may fail)", _FRPC_EXE)
        return
    if os.name != "nt":
        _FRPC_EXE.chmod(0o755)
    _frpc_proc = subprocess.Popen(
        [str(_FRPC_EXE), "-c", str(_FRPC_INI)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(_TOOLS_DIR),
    )
    # Wait up to 5 s for the SOCKS5 port to appear
    for _ in range(10):
        time.sleep(0.5)
        if _socks5_alive():
            _log.info("frpc: SOCKS5 proxy ready on :1080")
            return
    _log.warning("frpc: started but :1080 not yet listening — continuing anyway")


def _stop_frpc() -> None:
    if _frpc_proc and _frpc_proc.poll() is None:
        _frpc_proc.terminate()
        _log.info("frpc: stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_frpc()
    yield
    _stop_frpc()


app = FastAPI(title="InfoDashboard", version="0.1.0", lifespan=lifespan)

_FRONTEND = Path(__file__).resolve().parent / "frontend" / "index.html"


# ── Request / response models ────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    user_request: str
    db_config: DBConfig | None = None   # overrides env vars when provided
    max_retries: int = 2


def _resolve_db_config(override: DBConfig | None) -> DBConfig:
    """Use override if provided, otherwise build from environment variables."""
    if override:
        return override
    return DBConfig(
        socks5_host=os.getenv("SOCKS5_HOST", "127.0.0.1"),
        socks5_port=int(os.getenv("SOCKS5_PORT", "1080")),
        socks5_user=os.getenv("SOCKS5_USER", ""),
        socks5_pass=os.getenv("SOCKS5_PASS", ""),
        db_host=os.getenv("DB_HOST", ""),
        db_port=int(os.getenv("DB_PORT", "1433")),
        db_user=os.getenv("DB_USER", ""),
        db_pass=os.getenv("DB_PASS", ""),
        db_name=os.getenv("DB_NAME", ""),
    )


# ── Routes ───────────────────────────────────────────────────────────────────

@app.post("/api/generate")
async def generate_dashboard(req: GenerateRequest):
    """
    Stream the pipeline as Server-Sent Events.

    The client reads the stream and updates UI as each agent_start / text_delta /
    action / done event arrives — identical to how OpenMAIC's frontend works.
    """
    db_config = _resolve_db_config(req.db_config)

    async def event_stream():
        async for event in generate(req.user_request, db_config, req.max_retries):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # prevent nginx from buffering SSE
            "Connection": "keep-alive",
        },
    )


@app.get("/api/dashboards")
def get_dashboards():
    try:
        return {"dashboards": list_dashboards()}
    except Exception as exc:
        return {"dashboards": [], "error": str(exc)}


@app.delete("/api/dashboards/{container_id}")
def delete_dashboard(container_id: str):
    stop_dashboard(container_id)
    return {"status": "stopped", "id": container_id}


@app.get("/", response_class=HTMLResponse)
def index():
    if not _FRONTEND.exists():
        return HTMLResponse("<h1>Frontend not found</h1>", status_code=404)
    return HTMLResponse(_FRONTEND.read_text(encoding="utf-8"))


# ── Dev entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True, reload_excludes=["generated/*"])
