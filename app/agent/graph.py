# -*- coding: utf-8 -*-
"""LangGraph 主图（PRD 第 4.1 节的状态机）。

    START
      -> normalize_input
      -> classify_and_extract
           ├─ ask_clarify  (缺必填槽位)      -> persist -> END
           ├─ chat         (闲聊兜底 INT-07) -> persist -> END
           ├─ retrieve     (知识类意图)  ┐
           └─ call_tools   (实时类意图)  ┘
      -> call_tools -> validate_plan -> compose -> persist -> END
      -> call_tools -> retrieve_hotel_fallback (酒店无结果时才回退 RAG)
      -> explain      (解释上一轮推荐理由) -> persist -> END

注意 retrieve 之后仍会进 call_tools：行程规划这类意图既需要知识库也需要天气，
两条证据在 call_tools 里合并成同一个证据池。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent import nodes
from app.agent.memory import make_async_checkpointer, make_checkpointer
from app.agent.state import AgentState

# route 节点的返回值 -> 真实节点名
ROUTE_TARGETS = {
    "ask": "ask_clarify",
    "chat": "chat",
    "retrieve": "retrieve",
    "tools": "call_tools",
    "explain": "explain",
}

_AGENT = None
_ASYNC_AGENT = None


def build_graph(checkpointer=None, with_memory: bool = True, store=None):
    """组装并编译主图。checkpointer=None 且 with_memory=True 时自动创建。"""
    builder = StateGraph(AgentState)
    builder.add_node("normalize_input", nodes.normalize_input)
    builder.add_node("classify_and_extract", nodes.classify_and_extract)
    builder.add_node("ask_clarify", nodes.ask_clarify)
    builder.add_node("chat", nodes.chat)
    builder.add_node("retrieve", nodes.retrieve)
    builder.add_node("retrieve_hotel_fallback", nodes.retrieve_hotel_fallback)
    builder.add_node("call_tools", nodes.call_tools)
    builder.add_node("validate_plan", nodes.validate_plan)
    builder.add_node("compose", nodes.compose)
    builder.add_node("explain", nodes.explain)
    builder.add_node("persist", nodes.persist)

    builder.add_edge(START, "normalize_input")
    builder.add_edge("normalize_input", "classify_and_extract")
    builder.add_conditional_edges("classify_and_extract", nodes.route, ROUTE_TARGETS)
    builder.add_edge("retrieve", "call_tools")
    builder.add_conditional_edges("call_tools", nodes.after_tools,
                                  {"retrieve": "retrieve_hotel_fallback",
                                   "validate": "validate_plan"})
    builder.add_edge("retrieve_hotel_fallback", "validate_plan")
    builder.add_edge("validate_plan", "compose")
    builder.add_edge("compose", "persist")
    builder.add_edge("ask_clarify", "persist")
    builder.add_edge("chat", "persist")
    builder.add_edge("explain", "persist")
    builder.add_edge("persist", END)

    if checkpointer is None and with_memory:
        checkpointer = make_checkpointer()
    if store is None:
        from app.agent.store import get_profile_store
        store = get_profile_store()
    return builder.compile(checkpointer=checkpointer, store=store)


def get_agent():
    """进程内单例（长驻服务用；命令行每次进程各建一次）。"""
    global _AGENT
    if _AGENT is None:
        _AGENT = build_graph()
    return _AGENT


async def get_async_agent():
    """异步图单例（FastAPI / SSE 用）。

    必须配 AsyncSqliteSaver：同步 saver 在 await graph.ainvoke() 时会抛
    NotImplementedError。
    """
    global _ASYNC_AGENT
    if _ASYNC_AGENT is None:
        checkpointer = await make_async_checkpointer()
        _ASYNC_AGENT = build_graph(checkpointer=checkpointer)
    return _ASYNC_AGENT


def mermaid() -> str:
    """导出 mermaid 流程图源码（答辩 PPT / 设计文档用）。"""
    try:
        return get_agent().get_graph().draw_mermaid()
    except Exception as exc:
        return "导出失败：%s" % exc
