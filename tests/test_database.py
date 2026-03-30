"""
Unit tests for tools/database.py — format_columns helper (no real DB needed).
"""
from __future__ import annotations

from tools.database import format_columns


def test_format_columns_single_table():
    columns = {
        "orders": [
            {"name": "id", "type": "int", "nullable": False, "is_pk": True},
            {"name": "amount", "type": "decimal", "nullable": True, "is_pk": False},
        ]
    }
    result = format_columns(columns)
    assert "[orders]" in result
    assert "id [PK] (int)" in result
    assert "amount (decimal?)" in result


def test_format_columns_multiple_tables():
    columns = {
        "t1": [{"name": "a", "type": "int", "nullable": False, "is_pk": False}],
        "t2": [{"name": "b", "type": "varchar", "nullable": True, "is_pk": False}],
    }
    result = format_columns(columns)
    assert "[t1]" in result
    assert "[t2]" in result


def test_format_columns_empty():
    assert format_columns({}) == ""


def test_format_columns_nullable_flag():
    columns = {
        "t": [
            {"name": "col1", "type": "int", "nullable": False, "is_pk": False},
            {"name": "col2", "type": "varchar", "nullable": True, "is_pk": False},
        ]
    }
    result = format_columns(columns)
    assert "col1 (int)" in result      # not nullable — no "?"
    assert "col2 (varchar?)" in result  # nullable — has "?"
