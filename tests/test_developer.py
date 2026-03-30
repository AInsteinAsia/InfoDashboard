"""
Unit tests for developer node helpers — pure parsing logic, no LLM calls.
"""
from __future__ import annotations

from orchestration.nodes.developer import _extract_code


def test_extract_code_parses_delimiters():
    raw = (
        "===APP_PY_START===\n"
        "import streamlit as st\nst.write('hello')\n"
        "===APP_PY_END===\n"
        "===REQUIREMENTS_START===\n"
        "streamlit>=1.40.0\n"
        "===REQUIREMENTS_END==="
    )
    result = _extract_code(raw)
    assert "import streamlit" in result.app_py
    assert "streamlit>=1.40.0" in result.requirements_txt


def test_extract_code_returns_defaults_when_delimiters_missing():
    result = _extract_code("some unstructured response")
    assert result.app_py == ""
    assert "streamlit" in result.requirements_txt


def test_extract_code_strips_whitespace():
    raw = (
        "===APP_PY_START===\n\n"
        "   import streamlit as st   \n\n"
        "===APP_PY_END===\n"
        "===REQUIREMENTS_START===\n"
        "  streamlit>=1.40.0  \n"
        "===REQUIREMENTS_END==="
    )
    result = _extract_code(raw)
    assert result.app_py == "import streamlit as st"
    assert result.requirements_txt == "streamlit>=1.40.0"


def test_extract_code_handles_multiline_app():
    app_content = "line1\nline2\nline3"
    raw = (
        f"===APP_PY_START===\n{app_content}\n===APP_PY_END===\n"
        "===REQUIREMENTS_START===\nstreamlit\n===REQUIREMENTS_END==="
    )
    result = _extract_code(raw)
    assert result.app_py == app_content
