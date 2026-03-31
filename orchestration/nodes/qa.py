"""
QA & Security node.

Responsibility:
  Statically validate the generated Streamlit code before Docker deployment.
  This node requires NO LLM — it's fast, deterministic, and cheap.

Checks performed:
  1. Python syntax (ast.parse)
  2. Parameterized-query enforcement (no f-string/concat SQL)
  3. Dangerous code patterns (eval, exec, os.system, subprocess)
  4. Mandatory DB connection template presence
  5. Bandit high-severity scan (best-effort; skipped if not installed)

If all checks pass → route to deployer.
If any fail → build structured feedback → route back to developer (up to max_retries).
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile

from langchain_core.runnables import RunnableConfig

from orchestration._llm import emit
from orchestration.state import DashboardState, QAResult

AGENT_ID = "qa"
AGENT_NAME = "QA & 安全检查"
AGENT_ICON = "🛡️"

# Patterns that suggest SQL injection via string building.
# NOTE: pytds uses %s as the standard parameterized placeholder, so
# cursor.execute("SELECT ... WHERE x = %s", (val,)) is CORRECT — do NOT flag %s.
_SQL_INJECT_PATTERNS: list[tuple[str, str]] = [
    # f-string directly inside cursor.execute(f"...")
    (r'cursor\.execute\s*\(\s*f["\']', "在 cursor.execute() 中直接使用 f-string 拼接 SQL"),
    # string literal + concatenation directly inside cursor.execute("..." + ...)
    (r'cursor\.execute\s*\(\s*["\'][^"\']*["\'\s]*\s*\+\s*', "在 cursor.execute() 中使用 + 拼接 SQL"),
    # .format() chained directly on the string passed to cursor.execute
    (r'cursor\.execute\s*\(.*?\.format\s*\(', "在 cursor.execute() 中使用 .format() 拼接 SQL"),
]

# Patterns that are outright dangerous in generated code
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r'\beval\s*\(', "禁止使用 eval()"),
    (r'\bexec\s*\(', "禁止使用 exec()"),
    (r'os\.system\s*\(', "禁止使用 os.system()"),
    (r'subprocess\.(call|run|Popen)\s*\(', "禁止使用 subprocess（安全风险）"),
    (r'__import__\s*\(', "禁止动态导入"),
    (r'open\s*\(.*?,\s*["\']w', "禁止在 Streamlit app 中写文件"),
]


async def run(state: DashboardState, config: RunnableConfig) -> dict:
    await emit(config, {
        "type": "agent_start",
        "data": {"agentId": AGENT_ID, "agentName": AGENT_NAME, "agentIcon": AGENT_ICON},
    })

    code = state.get("generated_code")
    if not code or not code.app_py:
        result = QAResult(
            passed=False,
            issues=["未生成代码"],
            feedback="developer 未生成有效的 app.py，请重新生成完整代码。",
        )
        await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
        return {"qa_result": result}

    issues: list[str] = []

    # ── Check 1: Python syntax ────────────────────────────────────────────────
    await emit(config, {"type": "thinking", "data": {"stage": "syntax", "message": "检查 Python 语法..."}})
    try:
        ast.parse(code.app_py)
    except SyntaxError as exc:
        issues.append(f"Python 语法错误（第 {exc.lineno} 行）：{exc.msg}")

    # ── Check 2: SQL injection patterns ──────────────────────────────────────
    await emit(config, {"type": "thinking", "data": {"stage": "sql_check", "message": "检查 SQL 注入风险..."}})
    for pattern, desc in _SQL_INJECT_PATTERNS:
        if re.search(pattern, code.app_py, re.DOTALL):
            issues.append(f"SQL 注入风险：{desc}")

    # ── Check 2b: unescaped % in LIKE (pytds treats % as format specifier) ───
    await emit(config, {"type": "thinking", "data": {"stage": "percent_check", "message": "检查 LIKE 子句 % 转义..."}})
    if re.search(r"LIKE\s+['\"]%[^%]|LIKE\s+['\"][^'\"]*[^%]%['\"]", code.app_py):
        issues.append("SQL LIKE 子句含未转义的 %（pytds 会报 string formatting 错误），必须写为 %%，如 LIKE '%%value%%'")

    # ── Check 3: Dangerous code ───────────────────────────────────────────────
    for pattern, desc in _DANGEROUS_PATTERNS:
        if re.search(pattern, code.app_py):
            issues.append(f"危险代码：{desc}")

    # ── Check 4: Mandatory DB template ───────────────────────────────────────
    if "get_conn" not in code.app_py:
        issues.append("缺少 get_conn() 函数（必须使用标准数据库连接模板）")
    if "socks.set_default_proxy" not in code.app_py:
        issues.append("缺少 SOCKS5 代理设置（socks.set_default_proxy）")
    if "pytds" not in code.app_py:
        issues.append("未使用 pytds 连接数据库")

    # ── Check 5: Bandit high-severity (best-effort) ───────────────────────────
    await emit(config, {"type": "thinking", "data": {"stage": "bandit", "message": "运行 Bandit 安全扫描..."}})
    bandit_issues = _run_bandit(code.app_py)
    issues.extend(bandit_issues)

    passed = len(issues) == 0
    feedback = (
        "代码通过所有检查。" if passed
        else "请修复以下问题后重新生成代码：\n" + "\n".join(f"  - {i}" for i in issues)
    )

    await emit(config, {
        "type": "action",
        "data": {
            "actionName": "qa_complete",
            "params": {"passed": passed, "issueCount": len(issues), "issues": issues},
        },
    })
    await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})

    return {"qa_result": QAResult(passed=passed, issues=issues, feedback=feedback)}


def _run_bandit(code: str) -> list[str]:
    """Run bandit and return HIGH severity findings. Returns [] on any error."""
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp = f.name
        proc = subprocess.run(
            [sys.executable, "-m", "bandit", "-ll", "-q", tmp],
            capture_output=True, text=True, timeout=20,
        )
        os.unlink(tmp)
        if proc.returncode == 1 and "HIGH" in proc.stdout:
            # Extract just the high-severity lines
            lines = [l for l in proc.stdout.splitlines() if "HIGH" in l or "Issue:" in l]
            return [f"Bandit HIGH：{' '.join(lines[:3])}"]
    except Exception:
        pass
    return []
