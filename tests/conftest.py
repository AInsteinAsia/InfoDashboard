"""
Shared fixtures for InfoDashboard tests.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from orchestration.state import (
    DBConfig,
    DeploymentInfo,
    GeneratedCode,
    QAResult,
    RequirementsSpec,
)


@pytest.fixture
def db_config() -> DBConfig:
    return DBConfig(
        db_host="test-host",
        db_port=1433,
        db_user="testuser",
        db_pass="testpass",
        db_name="testdb",
    )


@pytest.fixture
def sample_requirements() -> RequirementsSpec:
    return RequirementsSpec(
        title="产量看板",
        description="显示每日产量趋势",
        metrics=["产量", "合格率"],
        dimensions=["日期", "产线"],
        chart_types=["bar", "line"],
        filters=[],
        relevant_tables=["production_daily"],
        sql_hints="SELECT date, qty FROM production_daily",
    )


@pytest.fixture
def valid_app_py() -> str:
    return '''\
import socks
import socket as _socket
import os as _os

_s5_host = _os.environ.get("SOCKS5_HOST", "127.0.0.1")
_s5_port = int(_os.environ.get("SOCKS5_PORT", "1080"))
_s5_user = _os.environ.get("SOCKS5_USER") or None
_s5_pass = _os.environ.get("SOCKS5_PASS") or None
if _s5_user:
    socks.set_default_proxy(socks.SOCKS5, _s5_host, _s5_port, username=_s5_user, password=_s5_pass)
else:
    socks.set_default_proxy(socks.SOCKS5, _s5_host, _s5_port)
_socket.socket = socks.socksocket

import pytds

def get_conn():
    return pytds.connect(
        server=_os.environ["DB_HOST"],
        port=int(_os.environ.get("DB_PORT", "1433")),
        user=_os.environ["DB_USER"],
        password=_os.environ["DB_PASS"],
        database=_os.environ["DB_NAME"],
        timeout=30,
    )

import streamlit as st
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

st.set_page_config(layout="wide", page_title="产量看板")
st_autorefresh(interval=180_000, key="autorefresh")

@st.cache_data(ttl=180)
def load_data():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT date, qty FROM production_daily WHERE date >= %s", ("2024-01-01",))
    rows = cursor.fetchall()
    conn.close()
    return pd.DataFrame(rows, columns=["date", "qty"])

try:
    df = load_data()
    fig = px.bar(df, x="date", y="qty", title="每日产量")
    st.plotly_chart(fig, use_container_width=True)
except Exception as e:
    st.error(f"数据加载失败：{e}")
'''


@pytest.fixture
def mock_generate_success(sample_requirements: RequirementsSpec):
    """Mock orchestration.graph.generate to yield a done event immediately."""
    async def _fake_generate(user_request, db_config, max_retries=2):
        yield {"type": "agent_start", "data": {"agentId": "analyst", "agentName": "需求分析官", "agentIcon": "🔍"}}
        yield {"type": "agent_end", "data": {"agentId": "analyst"}}
        yield {"type": "done", "data": {"dashboardUrl": "http://localhost:8501", "port": 8501, "dashboardId": "dashboard-abc123"}}

    with patch("main.generate", side_effect=_fake_generate):
        yield


@pytest.fixture
def mock_generate_error():
    """Mock orchestration.graph.generate to yield an error event."""
    async def _fake_generate(user_request, db_config, max_retries=2):
        yield {"type": "error", "data": {"message": "数据库连接失败"}}

    with patch("main.generate", side_effect=_fake_generate):
        yield


@pytest.fixture
def client(mock_generate_success):
    """TestClient with the generate pipeline mocked to succeed."""
    # Patch lifespan so frpc is not started during tests
    with patch("main._start_frpc"), patch("main._stop_frpc"):
        from main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


@pytest.fixture
def client_error(mock_generate_error):
    """TestClient with the generate pipeline mocked to return an error."""
    with patch("main._start_frpc"), patch("main._stop_frpc"):
        from main import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
