# -*- coding: utf-8 -*-
"""LangGraph 主图（PRD 第 4.1 节的状态机）。

    START
      -> normalize_input
      -> classify_and_extract
           ├─ ask_clarify  (缺必填槽位)      -> persist -> END
           ├─ chat         (闲聊兜底 INT-07) -> persist -> END
           └─ call_tools   (所有旅行意图先调 API)
      -> call_tools -> retrieve (工具成功后补充 RAG) -> validate_plan
      -> compose -> persist -> END
      -> explain      (解释上一轮推荐理由) -> persist -> END
      -> reuse/regenerate (相同问题或要求重新回答) -> persist -> END

统一顺序是：工具/API 优先，RAG 后补。实时字段与知识背景进入同一个证据池，
但冲突时实时工具证据优先。
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
    "tools": "call_tools",
    "explain": "explain",
    "reuse": "reuse",
    "regenerate": "regenerate",
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
    builder.add_node("call_tools", nodes.call_tools)
    builder.add_node("validate_plan", nodes.validate_plan)
    builder.add_node("compose", nodes.compose)
    builder.add_node("explain", nodes.explain)
    builder.add_node("reuse", nodes.reuse_previous)
    builder.add_node("regenerate", nodes.regenerate_previous)
    builder.add_node("persist", nodes.persist)

    builder.add_edge(START, "normalize_input")
    builder.add_edge("normalize_input", "classify_and_extract")
    builder.add_conditional_edges("classify_and_extract", nodes.route, ROUTE_TARGETS)
    builder.add_conditional_edges("call_tools", nodes.after_tools,
                                  {"retrieve": "retrieve",
                                   "validate": "validate_plan"})
    builder.add_edge("retrieve", "validate_plan")
    builder.add_edge("validate_plan", "compose")
    builder.add_edge("compose", "persist")
    builder.add_edge("ask_clarify", "persist")
    builder.add_edge("chat", "persist")
    builder.add_edge("explain", "persist")
    builder.add_edge("reuse", "persist")
    builder.add_edge("regenerate", "persist")
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
