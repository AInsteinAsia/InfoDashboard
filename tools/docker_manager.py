"""
Docker SDK wrapper for building and managing dashboard containers.

Each generated dashboard gets:
  - A unique ID (dashboard-<hex8>)
  - Its own image: info-dashboard:<id>
  - A container running on a unique port (8501–8600)
  - --network host so the container can reach the SOCKS5 proxy on 127.0.0.1:1080

Note on Windows/Mac:
  --network host works natively on Linux. On Docker Desktop (Win/Mac), use
  DOCKER_HOST_NETWORK=false and the container will reach the proxy via
  host.docker.internal instead.
"""
from __future__ import annotations

import os
import socket as _socket

import docker
from docker.errors import ImageNotFound, NotFound

_client: docker.DockerClient | None = None

LABEL = "managed-by=info-dashboard"


def get_client() -> docker.DockerClient:
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def find_free_port(start: int = 8501, end: int = 8600) -> int:
    """Return the first port in [start, end] not already bound or used by a container."""
    used: set[int] = set()
    for container in get_client().containers.list():
        for bindings in container.ports.values():
            if bindings:
                for b in bindings:
                    try:
                        used.add(int(b["HostPort"]))
                    except (KeyError, ValueError):
                        pass

    for port in range(start, end + 1):
        if port in used:
            continue
        with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port

    raise RuntimeError(f"No free port found in range {start}–{end}")


def build_image(build_path: str, tag: str) -> None:
    """Build a Docker image from the given directory (blocking)."""
    client = get_client()
    _image, _logs = client.images.build(path=build_path, tag=tag, rm=True, forcerm=True)


def run_dashboard(image_name: str, port: int, env_vars: dict[str, str], extra_labels: dict[str, str] | None = None) -> str:
    """Start a dashboard container and return its container ID.

    Always uses explicit port mapping so the dashboard is reachable from the host
    on all platforms (Linux, Windows Docker Desktop, Mac).

    SOCKS5 access from inside the container:
    - Windows/Mac: Docker Desktop automatically resolves host.docker.internal → host IP
    - Linux:       --add-host=host.docker.internal:host-gateway achieves the same thing
                   (requires Docker 20.10+)
    """
    client = get_client()

    # Containers can't reach 127.0.0.1 (that's themselves), so we route
    # SOCKS5 traffic through the host's special DNS name.
    env_vars = {
        **env_vars,
        "SOCKS5_HOST": "host.docker.internal",
    }

    extra_hosts = {}
    if os.name != "nt":
        # On Linux, Docker doesn't add host.docker.internal by default
        extra_hosts = {"host.docker.internal": "host-gateway"}

    container = client.containers.run(
        image_name,
        detach=True,
        ports={"8501/tcp": port},       # map container :8501 → host :<port>
        environment=env_vars,
        extra_hosts=extra_hosts,
        labels={"managed-by": "info-dashboard", **(extra_labels or {})},
        restart_policy={"Name": "unless-stopped"},
    )
    return container.id


def stop_dashboard(container_id: str) -> None:
    """Stop and remove a dashboard container, its image, and generated files."""
    import shutil
    from pathlib import Path
    client = get_client()
    image_tag = None
    generated_dir = None
    try:
        c = client.containers.get(container_id)
        image_tag = c.image.tags[0] if c.image.tags else None
        generated_dir = c.labels.get("generated-dir")
        c.stop(timeout=10)
        c.remove()
    except NotFound:
        pass
    if image_tag:
        remove_image(image_tag)
    if generated_dir:
        shutil.rmtree(generated_dir, ignore_errors=True)


def list_dashboards() -> list[dict]:
    """List all running dashboard containers."""
    client = get_client()
    result = []
    for c in client.containers.list(filters={"label": "managed-by=info-dashboard"}):
        ports = {}
        for internal, bindings in c.ports.items():
            if bindings:
                ports[internal] = bindings[0].get("HostPort")
        result.append({
            "id": c.id[:12],
            "name": c.name,
            "status": c.status,
            "image": c.image.tags[0] if c.image.tags else "unknown",
            "ports": ports,
            "dashboard_id": c.labels.get("dashboard-id", ""),
        })
    return result


def remove_image(tag: str) -> None:
    """Remove a dashboard image (best-effort)."""
    client = get_client()
    try:
        client.images.remove(tag, force=True)
    except ImageNotFound:
        pass
