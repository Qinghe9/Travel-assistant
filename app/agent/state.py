# -*- coding: utf-8 -*-
"""LangGraph 的共享状态（PRD 第 4.1 节的 State）。

设计取舍：messages 用纯 dict（{"role": ..., "content": ...}）而不是
LangChain 的消息对象。原因有两个：
1. SQLite checkpointer 会把 state 序列化，纯 dict 不会有兼容问题；
2. 轨迹要落盘审计，dict 直接 json.dumps 即可，不需要额外转换。
"""

from __future__ import annotations

from operator import add
from typing import Annotated, TypedDict


class AgentState(TypedDict, total=False):
    # 会话
    thread_id: str
    user_id: str
    trace_id: str
    thinking: bool
    smart_search: bool
    messages: Annotated[list, add]
    query: str
    normalized_query: str

    # 理解层（PRD FR-01）
    intent: str
    intents: list
    tasks: list
    confidence: float
    slots: dict
    destination_source: str
    missing: list
    pending_intent: str
    pending_missing: list
    date_updated_at: str
    profile: dict
    route_source: str
    follow_up: str
    last_turn: dict
    urgent: bool
    emotion: float

    # 证据层（PRD 第 6.4 节）
    evidence: list
    citations: list
    evidence_meta: dict
    tool_results: list

    # 校验与输出
    plan: dict
    answer: str
    degraded: bool
    no_evidence: bool
    evidence_binding_rate: float

    # 可追溯
    trace: Annotated[list, add]


def trace_event(node: str, latency_ms: int = 0, status: str = "ok", detail: str = "", **extra) -> dict:
    """构造一条轨迹记录（纯 dict，便于落盘）。"""
    event = {"node": node, "status": status, "latency_ms": int(latency_ms), "detail": detail}
    if extra:
        event["extra"] = extra
    return event
