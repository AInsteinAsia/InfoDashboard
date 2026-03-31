"""
Web Developer node.

Responsibility:
  Given a RequirementsSpec + DB schema, generate a complete, runnable Streamlit app
  (app.py + requirements.txt) that queries SQL Server and renders interactive charts.

Output format: the LLM must wrap code in fixed delimiters so _extract_code() can
parse it reliably even when the response is streamed incrementally.

On retry: the QA feedback is injected into the prompt so the LLM fixes specific issues
rather than regenerating from scratch.
"""
from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from orchestration._llm import emit, stream_llm
from orchestration.state import DashboardState, GeneratedCode

AGENT_ID = "developer"
AGENT_NAME = "Web 开发工程师"
AGENT_ICON = "💻"

# ── DB connection boilerplate injected into every generated app ───────────────
# This block is the only permitted way to connect to the database.
# Credentials arrive via environment variables injected by Docker.
_DB_TEMPLATE = '''\
import socks
import socket as _socket
import os as _os

# Set up SOCKS5 proxy BEFORE importing pytds (C-free pytds uses Python socket)
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
'''

SYSTEM_PROMPT = f"""\
你是一名专业的 Streamlit 数据看板工程师，专为工厂制造业生成 SQL Server 数据可视化应用。

## 强制规则

### 代码结构
1. 生成完整可运行的 app.py，不省略、不截断、不使用 "..." 占位
2. 数据库连接必须原封不动地包含以下模板（不得修改连接部分）：

```python
{_DB_TEMPLATE}
```

3. 所有 SQL 必须使用参数化查询：`cursor.execute(sql, (param1, param2))`，严禁字符串拼接或 f-string
4. 使用 `@st.cache_data(ttl=180)` 缓存所有查询函数（3 分钟）
5. 使用 `plotly.express` 绘图，`st.plotly_chart(fig, use_container_width=True)`
6. 筛选控件全部放在 `st.sidebar`
7. 用 `st.error()` 捕获并展示数据库异常，不要让 app crash
8. 第一行：`st.set_page_config(layout="wide", page_title="<看板标题>")`
9. 在 `st.set_page_config` 之后立即加自动刷新：`from streamlit_autorefresh import st_autorefresh` / `st_autorefresh(interval=180_000, key="autorefresh")`

### SQL Server 2016 方言（必须严格遵守）
数据库为 **Microsoft SQL Server 2016**，以下规则不可违反：

**禁止使用，及其替代写法：**
- `LIMIT n` → 用 `SELECT TOP n` 或 `OFFSET n ROWS FETCH NEXT n ROWS ONLY`
- `NOW()` → 用 `GETDATE()`
- `IFNULL(a, b)` / `NVL(a, b)` → 用 `ISNULL(a, b)`
- `IF(cond, a, b)` → 用 `CASE WHEN cond THEN a ELSE b END`
- `GROUP_CONCAT(col)` → 用 `(SELECT col + ',' FOR XML PATH(''))` 子查询（2016 无 STRING_AGG）
- `TRIM()` → 用 `LTRIM(RTRIM())`（2016 无 TRIM）
- `CONCAT_WS()` → 用 `col1 + sep + col2`（2016 无 CONCAT_WS）
- `DATE_FORMAT(d, fmt)` → 用 `FORMAT(d, fmt)` 或 `CONVERT(VARCHAR, d, 120)`
- `DATE_TRUNC(unit, d)` → 用 `CAST(d AS DATE)` 或 `DATEADD(day, DATEDIFF(day,0,d), 0)`
- `STR_TO_DATE(s, fmt)` → 用 `TRY_CAST(s AS DATE)` 或 `CONVERT(DATE, s, 120)`
- `TRUE` / `FALSE` → 用 `1` / `0`
- `WHERE` 子句不能使用 `SELECT` 中定义的列别名，需重复表达式或用子查询/CTE
- `FROM` 子句中的子查询必须有别名，例如 `(SELECT ...) AS sub`

**pytds 驱动特殊要求：**
- SQL 中的 `%` 必须写成 `%%`（如 `LIKE '%%keyword%%'`），否则报 string formatting 错误
- `SELECT DISTINCT` 时 `ORDER BY` 的字段必须出现在 `SELECT` 列表中
- 字符串列的 JOIN 或比较需加 `COLLATE Chinese_PRC_CI_AS`，如 `a.col = b.col COLLATE Chinese_PRC_CI_AS`

## 输出格式（必须严格遵守，用于代码提取）

===APP_PY_START===
[完整 app.py 代码，从 import 开始到最后一行]
===APP_PY_END===

===REQUIREMENTS_START===
[requirements.txt 内容，每行一个包]
===REQUIREMENTS_END===

requirements.txt 必须包含：streamlit>=1.40.0, pandas>=2.0.0, plotly>=5.0.0, python-tds>=1.15.0, pysocks>=1.7.1, streamlit-autorefresh>=1.0.0
"""


async def run(state: DashboardState, config: RunnableConfig) -> dict:
    is_retry = state.get("qa_result") is not None and not state["qa_result"].passed
    retry_count = state.get("retry_count", 0)
    if is_retry:
        retry_count += 1

    await emit(config, {
        "type": "agent_start",
        "data": {
            "agentId": AGENT_ID,
            "agentName": AGENT_NAME,
            "agentIcon": AGENT_ICON,
            "retry": retry_count if is_retry else 0,
        },
    })

    req = state["requirements"]
    schema = state.get("schema_info", "（无法获取表结构）")

    qa_feedback_section = ""
    if is_retry and state.get("qa_result"):
        qa_feedback_section = (
            "\n\n## ⚠️ QA 反馈（本次重试必须修复所有问题）\n"
            + state["qa_result"].feedback
        )

    user_prompt = (
        f"## 需求规格\n"
        f"标题：{req.title}\n"
        f"描述：{req.description}\n"
        f"指标：{', '.join(req.metrics)}\n"
        f"维度：{', '.join(req.dimensions)}\n"
        f"图表类型：{', '.join(req.chart_types)}\n"
        f"筛选器：{[f.model_dump() for f in req.filters]}\n"
        f"相关表：{', '.join(req.relevant_tables)}\n"
        f"SQL 提示：{req.sql_hints}\n\n"
        f"## 数据库表结构\n{schema}"
        f"{qa_feedback_section}\n\n"
        f"请生成完整的 Streamlit 看板代码。"
    )

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    raw = await stream_llm(messages, config, AGENT_ID)
    generated = _extract_code(raw)

    # Emit a preview of the generated code (first 600 chars) as a code_block event
    if generated.app_py:
        preview = generated.app_py[:600] + ("\n..." if len(generated.app_py) > 600 else "")
        await emit(config, {
            "type": "code_block",
            "data": {"filename": "app.py", "language": "python", "content": preview},
        })

    await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
    return {"generated_code": generated, "retry_count": retry_count}


def _extract_code(text: str) -> GeneratedCode:
    """Parse ===APP_PY_START=== / ===REQUIREMENTS_START=== delimiters."""
    app_match = re.search(
        r"===APP_PY_START===\s*\n(.*?)\n===APP_PY_END===", text, re.DOTALL
    )
    req_match = re.search(
        r"===REQUIREMENTS_START===\s*\n(.*?)\n===REQUIREMENTS_END===", text, re.DOTALL
    )

    app_py = app_match.group(1).strip() if app_match else ""
    requirements_txt = req_match.group(1).strip() if req_match else (
        "streamlit>=1.40.0\n"
        "pandas>=2.0.0\n"
        "plotly>=5.0.0\n"
        "python-tds>=1.15.0\n"
        "pysocks>=1.7.1\n"
        "streamlit-autorefresh>=1.0.0\n"
    )
    return GeneratedCode(app_py=app_py, requirements_txt=requirements_txt)
