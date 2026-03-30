"""
Unit tests for the QA node — pure static analysis, no LLM / network calls.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestration.nodes.qa import _run_bandit, run
from orchestration.state import GeneratedCode, QAResult


# ── _run_bandit ───────────────────────────────────────────────────────────────

def test_run_bandit_clean_code_returns_empty():
    clean = "x = 1 + 2\nprint(x)\n"
    result = _run_bandit(clean)
    assert result == []


def test_run_bandit_returns_empty_on_tool_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = _run_bandit("x = 1")
    assert result == []


# ── run() — async QA node ─────────────────────────────────────────────────────

def _make_config():
    queue = asyncio.Queue()
    return {"configurable": {"event_queue": queue}}, queue


def _make_state(app_py: str) -> dict:
    return {
        "generated_code": GeneratedCode(
            app_py=app_py,
            requirements_txt="streamlit>=1.40.0\n",
        )
    }


@pytest.mark.asyncio
async def test_qa_passes_valid_code(valid_app_py: str):
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(valid_app_py), config)
    assert result["qa_result"].passed is True
    assert result["qa_result"].issues == []


@pytest.mark.asyncio
async def test_qa_fails_on_syntax_error():
    bad_code = "def foo(:\n    pass\n"
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(bad_code), config)
    assert result["qa_result"].passed is False
    assert any("语法" in i for i in result["qa_result"].issues)


@pytest.mark.asyncio
async def test_qa_fails_on_fstring_sql_injection(valid_app_py: str):
    injected = valid_app_py + '\ncursor.execute(f"SELECT * FROM t WHERE x = {val}")\n'
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(injected), config)
    assert result["qa_result"].passed is False
    assert any("SQL 注入" in i for i in result["qa_result"].issues)


@pytest.mark.asyncio
async def test_qa_fails_on_concat_sql_injection(valid_app_py: str):
    injected = valid_app_py + '\ncursor.execute("SELECT * FROM t WHERE x = " + user_input)\n'
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(injected), config)
    assert result["qa_result"].passed is False
    assert any("SQL 注入" in i for i in result["qa_result"].issues)


@pytest.mark.asyncio
async def test_qa_fails_on_eval(valid_app_py: str):
    injected = valid_app_py + "\neval(user_input)\n"
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(injected), config)
    assert result["qa_result"].passed is False
    assert any("eval" in i for i in result["qa_result"].issues)


@pytest.mark.asyncio
async def test_qa_fails_on_missing_get_conn():
    code = "import streamlit as st\nst.write('hello')\n"
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        result = await run(_make_state(code), config)
    assert result["qa_result"].passed is False
    assert any("get_conn" in i for i in result["qa_result"].issues)


@pytest.mark.asyncio
async def test_qa_fails_when_no_code_generated():
    state = {"generated_code": None}
    config, queue = _make_config()
    result = await run(state, config)
    assert result["qa_result"].passed is False


@pytest.mark.asyncio
async def test_qa_emits_agent_start_and_end(valid_app_py: str):
    config, queue = _make_config()
    with patch("orchestration.nodes.qa._run_bandit", return_value=[]):
        await run(_make_state(valid_app_py), config)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    types = [e["type"] for e in events]
    assert "agent_start" in types
    assert "agent_end" in types
