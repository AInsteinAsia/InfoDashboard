"""
Unit tests for analyst node helpers — pure parsing logic, no LLM / DB calls.
"""
from __future__ import annotations

from orchestration.nodes.analyst import _parse_requirements
from orchestration.state import RequirementsSpec


def test_parse_requirements_valid_json():
    raw = """{
        "title": "产量看板",
        "description": "显示每日产量",
        "metrics": ["产量"],
        "dimensions": ["日期"],
        "chart_types": ["bar"],
        "filters": [],
        "relevant_tables": ["prod_daily"],
        "sql_hints": "SELECT date, qty FROM prod_daily"
    }"""
    spec = _parse_requirements(raw)
    assert spec.title == "产量看板"
    assert spec.metrics == ["产量"]
    assert spec.relevant_tables == ["prod_daily"]


def test_parse_requirements_strips_markdown_fence():
    raw = (
        "```json\n"
        '{"title":"看板","description":"desc","metrics":["x"],'
        '"dimensions":["d"],"chart_types":["bar"],'
        '"filters":[],"relevant_tables":[],"sql_hints":""}\n'
        "```"
    )
    spec = _parse_requirements(raw)
    assert spec.title == "看板"


def test_parse_requirements_returns_fallback_on_invalid_json():
    spec = _parse_requirements("not valid json at all {{{")
    assert isinstance(spec, RequirementsSpec)
    assert spec.title == "信息看板"   # fallback default


def test_parse_requirements_returns_fallback_on_empty():
    spec = _parse_requirements("")
    assert isinstance(spec, RequirementsSpec)


def test_parse_requirements_extracts_json_with_leading_text():
    """Gemini often adds prose before the JSON object."""
    raw = (
        "以下是根据您的需求生成的规格：\n\n"
        '{"title":"OA超期看板","description":"desc","metrics":["超期条数"],'
        '"dimensions":["人员"],"chart_types":["bar"],'
        '"filters":[],"relevant_tables":["oa_task"],"sql_hints":""}'
    )
    spec = _parse_requirements(raw)
    assert spec.title == "OA超期看板"
    assert spec.metrics == ["超期条数"]


def test_parse_requirements_handles_filter_specs():
    raw = """{
        "title": "T",
        "description": "D",
        "metrics": ["m"],
        "dimensions": ["d"],
        "chart_types": ["bar"],
        "filters": [{"field": "日期", "type": "date_range", "label": "日期范围"}],
        "relevant_tables": [],
        "sql_hints": ""
    }"""
    spec = _parse_requirements(raw)
    assert len(spec.filters) == 1
    assert spec.filters[0].type == "date_range"
    assert spec.filters[0].field == "日期"


def test_parse_requirements_ignores_unknown_filter_type():
    """Unknown filter type should cause Pydantic validation error → fallback."""
    raw = """{
        "title": "T",
        "description": "D",
        "metrics": [],
        "dimensions": [],
        "chart_types": [],
        "filters": [{"field": "x", "type": "unknown_type", "label": ""}],
        "relevant_tables": [],
        "sql_hints": ""
    }"""
    # Pydantic will reject the unknown type, _parse_requirements returns fallback
    spec = _parse_requirements(raw)
    assert isinstance(spec, RequirementsSpec)
