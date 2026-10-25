# -*- coding: utf-8 -*-
"""把 LangGraph 的执行过程转成事件流（PRD 第 5.3 节）。

事件类型与 PRD 对齐：
    meta   会话开始（thread_id / trace_id）
    step   某个节点执行完（node / latency_ms / status / detail）
    token  模型增量文本（打字机效果）
    final  最终答案与证据（客户端可用它校正已收到的 token）
    error  异常

实现要点（参考课程第 25 节）：
- 同时订阅 custom / updates / values 三种流；
- custom 收节点内部 writer 推的 token；
- updates 收每个节点返回的 trace，转成 step；
- values 保留最后一份完整状态，作为 final 的来源。
"""

from __future__ import annotations

import time


def _split(item):
    """兼容两种产出：单模式下直接给 chunk，多模式下给 (mode, chunk)。"""
    if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
        return item[0], item[1]
    return "custom", item


async def astream_answer(agent, inputs: dict, config: dict):
    """异步生成事件字典，由 SSE 层负责编码。"""
    started_at = time.perf_counter()
    first_token_ms = None
    thread_id = inputs.get("thread_id", "")
    trace_id = inputs.get("trace_id", "")
    yield {"type": "meta", "thread_id": thread_id, "trace_id": trace_id}

    final_state = {}
    async for item in agent.astream(inputs, config, stream_mode=["custom", "updates", "values"]):
        mode, chunk = _split(item)
        if mode == "custom":
            if isinstance(chunk, dict) and chunk.get("type"):
                if chunk.get("type") == "token" and first_token_ms is None:
                    first_token_ms = int((time.perf_counter() - started_at) * 1000)
                yield chunk
        elif mode == "updates":
            for _node, update in (chunk or {}).items():
                if not isinstance(update, dict):
                    continue
                for event in update.get("trace") or []:
                    if isinstance(event, dict):
                        yield {"type": "step", **event}
        elif mode == "values":
            if isinstance(chunk, dict):
                final_state = chunk

    citations = final_state.get("citations") or []
    yield {
        "type": "final",
        "thread_id": thread_id,
        "user_id": final_state.get("user_id", ""),
        "trace_id": trace_id,
        "ttft_ms": first_token_ms,
        "latency_ms": int((time.perf_counter() - started_at) * 1000),
        "query": final_state.get("query", ""),
        "normalized_query": final_state.get("normalized_query", ""),
        "intent": final_state.get("intent", ""),
        "intents": final_state.get("intents") or [],
        "tasks": final_state.get("tasks") or [],
        "confidence": final_state.get("confidence", 0.0),
        "slots": final_state.get("slots") or {},
        "destination_source": final_state.get("destination_source", ""),
        "missing": final_state.get("missing") or [],
        "answer": final_state.get("answer", ""),
        "citations": citations,
        "tool_refs": [c for c in citations if c.get("kind") == "tool"],
        "plan": final_state.get("plan") or {},
        "degraded": bool(final_state.get("degraded")),
        "no_evidence": bool(final_state.get("no_evidence")),
        "evidence_binding_rate": final_state.get("evidence_binding_rate", 0.0),
        "profile": final_state.get("profile") or {},
        "route_source": final_state.get("route_source", ""),
        "urgent": bool(final_state.get("urgent")),
        "emotion": final_state.get("emotion"),
    }
