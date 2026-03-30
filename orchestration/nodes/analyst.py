"""
Requirement Analyst node — exploratory schema-driven analysis.

Three-step flow (inspired by how a real analyst works):

  Step 1  get_table_list()
          Fetch all table names from the database (one lightweight query).
          ERP databases can have hundreds of tables — we don't want to dump
          all their columns into the LLM context.

  Step 2  LLM: pick relevant tables
          Given user request + all table names, the LLM selects the 3-8 most
          likely candidates based on name semantics alone. Fast, cheap call.

  Step 3  get_table_columns(selected_tables)
          Fetch full column details ONLY for the chosen tables.
          Now the LLM has real field names, types, and PK info to work with.

  Step 4  LLM: generate RequirementsSpec
          With accurate schema knowledge, produce the final structured spec
          (metrics, dimensions, chart types, filters, SQL hints with real names).
"""
from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig

from orchestration._llm import emit, get_llm, stream_llm
from orchestration.state import DashboardState, FilterSpec, RequirementsSpec
from tools.database import format_columns, get_table_columns, get_table_list

_log = logging.getLogger(__name__)

AGENT_ID = "analyst"
AGENT_NAME = "需求分析官"
AGENT_ICON = "🔍"

# ── Prompt: table selection (Step 2) ─────────────────────────────────────────
_TABLE_SELECT_PROMPT = """\
你是一名工厂数据分析专家。
用户想要的看板需求如下，数据库中所有可用表名如下列表。
请从中选出最相关的 3-8 张表，以 JSON 数组返回，只写表名，不要任何解释：

["表名1", "表名2", ...]
"""

# ── Prompt: requirements spec (Step 4) ───────────────────────────────────────
_SPEC_PROMPT = """\
你是一名工厂数据分析需求分析官。
根据用户需求和已探索的数据库表结构，生成精确的结构化规格。

输出格式（严格 JSON，不要任何 markdown 包裹或额外文字）：
{
  "title": "看板标题（简洁）",
  "description": "看板功能描述（1-2句话）",
  "metrics": ["合格率", "产量"],
  "dimensions": ["产线", "班次", "日期"],
  "chart_types": ["bar", "line"],
  "filters": [
    {"field": "日期", "type": "date_range", "label": "日期范围"},
    {"field": "产线", "type": "select", "label": "产线筛选"}
  ],
  "relevant_tables": ["真实表名1", "真实表名2"],
  "sql_hints": "使用真实字段名：合格率 = CAST(合格数字段 AS FLOAT)/总数字段*100；JOIN 条件：..."
}

规则：
- relevant_tables 和 sql_hints 中只能使用上面提供的真实表名和字段名
- chart_types 可选值：bar / line / pie / table / gauge
- filter type 可选值：date_range / select / text / number_range
"""


async def run(state: DashboardState, config: RunnableConfig) -> dict:
    await emit(config, {
        "type": "agent_start",
        "data": {"agentId": AGENT_ID, "agentName": AGENT_NAME, "agentIcon": AGENT_ICON},
    })

    db_cfg = state["db_config"]

    # ── Step 1: fetch all table names (lightweight) ───────────────────────────
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "table_list", "message": "正在获取数据库表列表..."},
    })
    try:
        all_tables = await get_table_list(db_cfg)
    except Exception as exc:
        err_msg = f"数据库连接失败：{exc}\n请确认 frpc visitor 已启动（frpc -c frpc-visitor.ini）"
        await emit(config, {
            "type": "error",
            "data": {"message": err_msg},
        })
        await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})
        # Return error without requirements → _route_after_analyst will go to END
        return {"schema_info": "", "error": err_msg}

    await emit(config, {
        "type": "action",
        "data": {
            "actionName": "tables_found",
            "params": {"count": len(all_tables), "sample": all_tables[:10]},
        },
    })

    # ── Step 2: LLM picks relevant tables ────────────────────────────────────
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "table_select", "message": f"从 {len(all_tables)} 张表中筛选相关表..."},
    })
    selected_tables = await _select_tables(state["user_request"], all_tables, config)

    await emit(config, {
        "type": "action",
        "data": {
            "actionName": "tables_selected",
            "params": {"tables": selected_tables},
        },
    })

    # ── Step 3: fetch column details for selected tables only ─────────────────
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "column_inspect", "message": f"探索表结构：{', '.join(selected_tables)}"},
    })
    try:
        columns = await get_table_columns(db_cfg, selected_tables)
        schema_info = format_columns(columns)
    except Exception as exc:
        schema_info = f"（字段查询失败：{exc}）"

    # ── Step 4: LLM generates RequirementsSpec with real column knowledge ─────
    await emit(config, {
        "type": "thinking",
        "data": {"stage": "spec_gen", "message": "正在生成需求规格..."},
    })
    raw = await stream_llm(
        [
            SystemMessage(content=_SPEC_PROMPT),
            HumanMessage(content=(
                f"用户需求：{state['user_request']}\n\n"
                f"已探索的表结构：\n{schema_info}"
            )),
        ],
        config,
        AGENT_ID,
    )

    requirements = _parse_requirements(raw)

    await emit(config, {
        "type": "action",
        "data": {
            "actionName": "requirements_ready",
            "params": {
                "title": requirements.title,
                "metrics": requirements.metrics,
                "tables": requirements.relevant_tables,
            },
        },
    })
    await emit(config, {"type": "agent_end", "data": {"agentId": AGENT_ID}})

    return {"schema_info": schema_info, "requirements": requirements}


async def _select_tables(user_request: str, all_tables: list[str], config: RunnableConfig) -> list[str]:
    """Call LLM with just table names to pick the relevant subset."""
    table_list_str = "\n".join(f"- {t}" for t in all_tables)
    llm = get_llm()
    msg = await llm.ainvoke([
        SystemMessage(content=_TABLE_SELECT_PROMPT),
        HumanMessage(content=(
            f"用户需求：{user_request}\n\n"
            f"所有表名：\n{table_list_str}"
        )),
    ])
    raw = msg.content if isinstance(msg.content, str) else ""

    # Parse the JSON array from the response
    try:
        text = raw.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:].strip()
        selected = json.loads(text)
        if isinstance(selected, list):
            # Validate: only keep names that actually exist in the DB
            valid = set(all_tables)
            return [t for t in selected if t in valid][:8]
    except Exception:
        pass

    # Fallback: simple keyword match between request and table names
    request_lower = user_request.lower()
    scored = [
        (t, sum(1 for word in t.lower().split("_") if word in request_lower))
        for t in all_tables
    ]
    scored.sort(key=lambda x: -x[1])
    return [t for t, _ in scored[:5]] or all_tables[:5]


def _parse_requirements(raw: str) -> RequirementsSpec:
    """Extract the JSON object from the LLM response and validate it."""
    text = raw.strip()

    # 1. Prefer content inside ```json ... ``` fences
    if "```" in text:
        for part in text.split("```"):
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break

    # 2. Fallback: find the first top-level {...} block anywhere in the response
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)

    try:
        data = json.loads(text)
        filters = [
            FilterSpec(**f) if isinstance(f, dict) else f
            for f in data.get("filters", [])
        ]
        data["filters"] = filters
        return RequirementsSpec(**data)
    except Exception as exc:
        _log.warning("需求规格解析失败，使用兜底值。原因：%s\n原始响应前300字：%s", exc, raw[:300])
        return RequirementsSpec(
            title="信息看板",
            description="数据分析看板",
            metrics=["数量"],
            dimensions=["日期"],
            chart_types=["bar"],
            filters=[],
            relevant_tables=[],
            sql_hints="",
        )
