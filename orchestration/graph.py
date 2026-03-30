"""
LangGraph pipeline definition.

Graph topology:

  START → analyst ──(db ok)──→ developer → qa ──(pass)──→ deployer → END
                  ↘(db fail)→ END               ↑___________(fail, retry < max)
                                                            (fail, retry >= max) → END

Key design choices (inspired by OpenMAIC):
- All state is explicit in DashboardState (no hidden server-side session).
- Nodes communicate with the SSE stream via an asyncio.Queue stored in
  LangGraph's `config["configurable"]["event_queue"]`.
- generate() is an async generator — the FastAPI route consumes it directly.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from langgraph.graph import END, START, StateGraph

from orchestration.nodes import analyst, deployer, developer, qa
from orchestration.state import DBConfig, DashboardEvent, DashboardState


# ── Routing ──────────────────────────────────────────────────────────────────

def _route_after_analyst(state: DashboardState) -> str:
    """Stop early if analyst couldn't connect to the DB or parse requirements."""
    if state.get("error") or state.get("requirements") is None:
        return END
    return "developer"


def _route_after_qa(state: DashboardState) -> str:
    result = state.get("qa_result")
    if result and result.passed:
        return "deployer"
    if state.get("retry_count", 0) >= state.get("max_retries", 2):
        return END
    return "developer"


# ── Graph construction ───────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(DashboardState)

    g.add_node("analyst", analyst.run)
    g.add_node("developer", developer.run)
    g.add_node("qa", qa.run)
    g.add_node("deployer", deployer.run)

    g.add_edge(START, "analyst")
    g.add_conditional_edges(
        "analyst",
        _route_after_analyst,
        {"developer": "developer", END: END},
    )
    g.add_edge("developer", "qa")
    g.add_conditional_edges(
        "qa",
        _route_after_qa,
        {
            "developer": "developer",   # QA failed, retry
            "deployer": "deployer",     # QA passed
            END: END,                   # QA failed and retries exhausted
        },
    )
    g.add_edge("deployer", END)

    return g.compile()


_graph = _build_graph()


# ── Public API ───────────────────────────────────────────────────────────────

async def generate(
    user_request: str,
    db_config: DBConfig,
    max_retries: int = 2,
) -> AsyncGenerator[DashboardEvent, None]:
    """
    Async generator that runs the full pipeline and yields typed SSE events.

    Usage::
        async for event in generate(request, db_config):
            yield f"data: {json.dumps(event)}\\n\\n"
    """
    queue: asyncio.Queue[DashboardEvent | None] = asyncio.Queue()

    initial_state: DashboardState = {
        "user_request": user_request,
        "db_config": db_config,
        "schema_info": "",
        "requirements": None,
        "generated_code": None,
        "qa_result": None,
        "deployment": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "error": None,
    }

    config = {"configurable": {"event_queue": queue}}

    async def _run_graph():
        try:
            final_state = await _graph.ainvoke(initial_state, config=config)
            # If the graph ended without a deployment, retries were exhausted — tell the user.
            if not final_state.get("deployment"):
                qa = final_state.get("qa_result")
                if qa and not qa.passed:
                    await queue.put({
                        "type": "error",
                        "data": {
                            "message": (
                                f"已达最大重试次数（{max_retries}），QA 检查仍未通过。\n"
                                f"最后一次问题：\n{qa.feedback}"
                            )
                        },
                    })
                elif final_state.get("error"):
                    await queue.put({"type": "error", "data": {"message": final_state["error"]}})
        except Exception as exc:
            await queue.put({"type": "error", "data": {"message": str(exc)}})
        finally:
            await queue.put(None)  # sentinel: signal generator to stop

    asyncio.create_task(_run_graph())

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event
