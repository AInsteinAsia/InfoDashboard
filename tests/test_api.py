"""
Integration tests for the FastAPI endpoints.

These tests mock the LangGraph pipeline so no real LLM / DB / Docker calls are made.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── GET / ─────────────────────────────────────────────────────────────────────

def test_index_returns_html(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


# ── POST /api/generate ────────────────────────────────────────────────────────

def test_generate_streams_sse_events(client: TestClient):
    resp = client.post(
        "/api/generate",
        json={"user_request": "显示每日产量趋势"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    # Parse SSE lines
    events = _parse_sse(resp.text)
    types = [e.get("type") for e in events]
    assert "agent_start" in types
    assert "done" in types


def test_generate_ends_with_done_sentinel(client: TestClient):
    resp = client.post(
        "/api/generate",
        json={"user_request": "产量看板"},
    )
    assert resp.text.strip().endswith("[DONE]")


def test_generate_error_pipeline(client_error: TestClient):
    resp = client_error.post(
        "/api/generate",
        json={"user_request": "任意请求"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    types = [e.get("type") for e in events]
    assert "error" in types


def test_generate_requires_user_request(client: TestClient):
    resp = client.post("/api/generate", json={})
    assert resp.status_code == 422   # Pydantic validation error


def test_generate_accepts_custom_db_config(client: TestClient):
    resp = client.post(
        "/api/generate",
        json={
            "user_request": "看板",
            "db_config": {
                "db_host": "10.0.0.1",
                "db_port": 1433,
                "db_user": "user",
                "db_pass": "pass",
                "db_name": "mydb",
            },
            "max_retries": 1,
        },
    )
    assert resp.status_code == 200


# ── GET /api/dashboards ───────────────────────────────────────────────────────

def test_list_dashboards_empty(client: TestClient):
    with patch("main.list_dashboards", return_value=[]):
        resp = client.get("/api/dashboards")
    assert resp.status_code == 200
    assert resp.json() == {"dashboards": []}


def test_list_dashboards_returns_containers(client: TestClient):
    fake = [{"id": "abc123", "port": 8501, "name": "info-dashboard:abc123"}]
    with patch("main.list_dashboards", return_value=fake):
        resp = client.get("/api/dashboards")
    assert resp.json()["dashboards"] == fake


def test_list_dashboards_handles_docker_error(client: TestClient):
    with patch("main.list_dashboards", side_effect=RuntimeError("Docker not running")):
        resp = client.get("/api/dashboards")
    assert resp.status_code == 200
    data = resp.json()
    assert data["dashboards"] == []
    assert "Docker not running" in data["error"]


# ── DELETE /api/dashboards/{id} ───────────────────────────────────────────────

def test_stop_dashboard(client: TestClient):
    with patch("main.stop_dashboard") as mock_stop:
        resp = client.delete("/api/dashboards/abc123")
    assert resp.status_code == 200
    assert resp.json() == {"status": "stopped", "id": "abc123"}
    mock_stop.assert_called_once_with("abc123")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_sse(text: str) -> list[dict]:
    """Extract JSON payloads from SSE data lines (skips [DONE])."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: ") and "[DONE]" not in line:
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events
