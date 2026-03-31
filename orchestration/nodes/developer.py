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
1. 生成完整可运行的 app.py，不省略、不截断、不使用 "..." 占位
2. 数据库连接必须原封不动地包含以下模板（不得修改连接部分）：

```python
{_DB_TEMPLATE}
```

3. 所有 SQL 必须使用参数化查询：`cursor.execute(sql, (param1, param2))`，严禁字符串拼接或 f-string
4. SQL 字符串中的 `%` 必须写成 `%%`（如 `LIKE '%%keyword%%'`），否则 pytds 会把它当成格式化符号报错
5. 涉及字符串列的 JOIN 或比较，必须加 `COLLATE Chinese_PRC_CI_AS` 避免排序规则冲突，例如：`a.col = b.col COLLATE Chinese_PRC_CI_AS`
6. 使用 `@st.cache_data(ttl=180)` 缓存所有查询函数（3 分钟）
7. 使用 `plotly.express` 绘图，`st.plotly_chart(fig, use_container_width=True)`
8. 筛选控件全部放在 `st.sidebar`
9. 用 `st.error()` 捕获并展示数据库异常，不要让 app crash
10. 第一行：`st.set_page_config(layout="wide", page_title="<看板标题>")`
11. 在 `st.set_page_config` 之后立即加自动刷新：`from streamlit_autorefresh import st_autorefresh` / `st_autorefresh(interval=180_000, key="autorefresh")`

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
