"""
Docker Deploy node.

Responsibility:
  1. Write generated files to disk (generated/<id>/).
  2. Build a Docker image.
  3. Run the container on a free port with DB credentials injected as env vars.
  4. Health-check the running service (HTTP GET with retries).
  5. Emit a "done" event with the dashboard URL.

The generated container uses --network host (Linux) so it can reach the frp SOCKS5
proxy on 127.0.0.1:1080 without any extra networking setup.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path

import aiohttp
from langchain_core.runnables import RunnableConfig

from orchestration._llm import emit
from orchestration.state import DashboardState, DeploymentInfo
from tools.docker_manager import alloc_port_and_run, build_image

AGENT_ID = "deployer"
AGENT_NAME = "部署运维工程师"
AGENT_ICON = "🚀"

_DOCKERFILE = """\
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--browser.gatherUsageStats=false"]
"""


async def run(state: DashboardState, config: RunnableConfig) -> dict:
    await emit(config, {
        "type": "agent_start",
        "data": {"agentId": AGENT_ID, "agentName": AGENT_NAME, "agentIcon": AGENT_ICON},
    })

    dashboard_id = f"dashboard-{uuid.uuid4().hex[:8]}"
    base_dir = Path(os.getenv("GENERATED_DIR", "./generated")) / dashboard_id
    base_dir.mkdir(parents=True, exist_ok=True)

    # ── Write files ───────────────────────────────────────────────────────────
    await emit(config, {"type": "thinking", "data": {"stage": "writing", "message": "写入应用文件..."}})
    (base_dir / "app.py").write_text(state["generated_code"].app_py, encoding="utf-8")
    (base_dir / "requirements.txt").write_text(
        state["generated_code"].requirements_txt, encoding="utf-8"
    )
    (base_dir / "Dockerfile").write_text(_DOCKERFILE, encoding="utf-8")

    # ── Docker build ──────────────────────────────────────────────────────────
    image_name = f"info-dashboard:{dashboard_id}"
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "docker_build", "message": f"构建镜像 {image_name}...（首次构建约需 1-2 分钟）"},
    })
    try:
        await asyncio.to_thread(build_image, str(base_dir), image_name)
    except Exception as exc:
        shutil.rmtree(base_dir, ignore_errors=True)
        await emit(config, {"type": "error", "data": {"message": f"Docker 构建失败：{exc}"}})
        await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
        return {"error": str(exc)}

    # ── Docker run (port allocation + start are atomic under _port_lock) ──────
    port_start = int(os.getenv("DASHBOARD_PORT_START", "8501"))
    port_end = int(os.getenv("DASHBOARD_PORT_END", "8600"))
    db = state["db_config"]
    env_vars = {
        "SOCKS5_HOST": db.socks5_host,
        "SOCKS5_PORT": str(db.socks5_port),
        "SOCKS5_USER": db.socks5_user,
        "SOCKS5_PASS": db.socks5_pass,
        "DB_HOST": db.db_host,
        "DB_PORT": str(db.db_port),
        "DB_USER": db.db_user,
        "DB_PASS": db.db_pass,
        "DB_NAME": db.db_name,
    }
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "docker_run", "message": "分配端口并启动容器..."},
    })
    try:
        container_id, port = await asyncio.to_thread(
            alloc_port_and_run, image_name, env_vars,
            {"generated-dir": str(base_dir), "dashboard-id": dashboard_id},
            port_start, port_end,
        )
    except Exception as exc:
        await emit(config, {"type": "error", "data": {"message": f"容器启动失败：{exc}"}})
        await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
        return {"error": str(exc)}

    # ── Health check ──────────────────────────────────────────────────────────
    url = f"http://localhost:{port}"
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "health_check", "message": f"等待服务就绪 {url} ..."},
    })
    healthy = await wait_healthy(url, max_wait=90, interval=3)

    if not healthy:
        msg = f"服务 {url} 未能在 90 秒内就绪，请检查容器日志：docker logs {container_id[:12]}"
        await emit(config, {"type": "error", "data": {"message": msg}})
        await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
        return {"error": msg}

    deployment = DeploymentInfo(
        dashboard_id=dashboard_id,
        port=port,
        url=url,
        container_id=container_id,
        image_name=image_name,
    )

    await emit(config, {
        "type": "done",
        "data": {"dashboardUrl": url, "port": port, "dashboardId": dashboard_id},
    })
    await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
    return {"deployment": deployment}


async def wait_healthy(url: str, max_wait: int = 90, interval: int = 3) -> bool:
    """Poll the dashboard URL until it responds 2xx/3xx or timeout."""
    timeout = aiohttp.ClientTimeout(total=5)
    for _ in range(max_wait // interval):
        await asyncio.sleep(interval)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status < 500:
                        return True
        except Exception:
            pass
    return False
