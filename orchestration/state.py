"""
State types for the InfoDashboard pipeline.

Philosophy (borrowed from OpenMAIC):
- All pipeline state is explicit and typed in DashboardState.
- Events are a typed union streamed to the client over SSE.
- Pydantic models for structured inter-node data; TypedDict for the LangGraph state.
"""
from __future__ import annotations

from typing import Literal, Union
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


# ── Structured data passed between nodes ────────────────────────────────────

class FilterSpec(BaseModel):
    field: str
    type: Literal["date_range", "select", "text", "number_range"]
    label: str = ""


class RequirementsSpec(BaseModel):
    title: str
    description: str
    metrics: list[str]                       # e.g. ["合格率", "产量"]
    dimensions: list[str]                    # e.g. ["产线", "班次", "日期"]
    chart_types: list[str]                   # e.g. ["bar", "line", "pie"]
    filters: list[FilterSpec] = Field(default_factory=list)
    relevant_tables: list[str] = Field(default_factory=list)
    sql_hints: str = ""                      # hints about JOINs, formulas, key fields


class GeneratedCode(BaseModel):
    app_py: str
    requirements_txt: str


class QAResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    feedback: str = ""   # structured feedback sent back to the developer node on retry


class DeploymentInfo(BaseModel):
    dashboard_id: str
    port: int
    url: str
    container_id: str
    image_name: str


class DBConfig(BaseModel):
    socks5_host: str = "127.0.0.1"
    socks5_port: int = 1080
    socks5_user: str = ""
    socks5_pass: str = ""
    db_host: str
    db_port: int = 1433
    db_user: str
    db_pass: str
    db_name: str


# ── LangGraph pipeline state ─────────────────────────────────────────────────

class DashboardState(TypedDict):
    # ── Input (set once at start, never mutated) ────────────────────────────
    user_request: str
    db_config: DBConfig

    # ── Populated by analyst node ───────────────────────────────────────────
    schema_info: str
    requirements: RequirementsSpec | None

    # ── Populated by developer node ─────────────────────────────────────────
    generated_code: GeneratedCode | None

    # ── Populated by qa node ────────────────────────────────────────────────
    qa_result: QAResult | None

    # ── Populated by deployer node ──────────────────────────────────────────
    deployment: DeploymentInfo | None

    # ── Loop control ────────────────────────────────────────────────────────
    retry_count: int     # how many times developer has been retried after QA failure
    max_retries: int

    error: str | None


# ── SSE event types (mirrors OpenMAIC's StatelessEvent union) ────────────────

class AgentStartEvent(TypedDict):
    type: Literal["agent_start"]
    data: dict   # {agentId, agentName, agentIcon}


class TextDeltaEvent(TypedDict):
    type: Literal["text_delta"]
    data: dict   # {content, agentId?}


class ThinkingEvent(TypedDict):
    type: Literal["thinking"]
    data: dict   # {stage, message?}


class CodeBlockEvent(TypedDict):
    type: Literal["code_block"]
    data: dict   # {filename, language, content}


class ActionEvent(TypedDict):
    type: Literal["action"]
    data: dict   # {actionName, params}


class AgentEndEvent(TypedDict):
    type: Literal["agent_end"]
    data: dict   # {agentId}


class DoneEvent(TypedDict):
    type: Literal["done"]
    data: dict   # {dashboardUrl, port, dashboardId}


class ErrorEvent(TypedDict):
    type: Literal["error"]
    data: dict   # {message}


DashboardEvent = Union[
    AgentStartEvent,
    TextDeltaEvent,
    ThinkingEvent,
    CodeBlockEvent,
    ActionEvent,
    AgentEndEvent,
    DoneEvent,
    ErrorEvent,
]
