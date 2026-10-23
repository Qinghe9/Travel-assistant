# -*- coding: utf-8 -*-
"""业务路由：对话、轨迹、反馈。"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import config as cfg
from app.agent.graph import get_agent, get_async_agent
from app.agent.store import clear_profile, load_profile
from app.agent.streaming import astream_answer
from app.api.sse import SSE_HEADERS, sse_frame
from app.models.schemas import ChatRequest, FeedbackRequest

router = APIRouter()

FEEDBACK_LOG = cfg.DATA / "agent" / "feedback.jsonl"


def _inputs(req: ChatRequest, thread_id: str, trace_id: str, user_id: str) -> dict:
    return {
        "messages": [{"role": "user", "content": req.message}],
        "thread_id": thread_id,
        "user_id": user_id,
        "trace_id": trace_id,
        "thinking": bool(req.thinking),
        "smart_search": bool(req.smart_search),
    }


def _reply_from_state(state: dict, trace_id: str) -> dict:
    return {
        "trace_id": trace_id,
        "thread_id": state.get("thread_id", ""),
        "user_id": state.get("user_id", ""),
        "query": state.get("query", ""),
        "intent": state.get("intent", ""),
        "confidence": state.get("confidence", 0.0),
        "slots": state.get("slots") or {},
        "missing": state.get("missing") or [],
        "answer": state.get("answer", ""),
        "citations": state.get("citations") or [],
        "tool_refs": [c for c in (state.get("citations") or []) if c.get("kind") == "tool"],
        "plan": state.get("plan") or {},
        "degraded": bool(state.get("degraded")),
        "no_evidence": bool(state.get("no_evidence")),
        "evidence_binding_rate": state.get("evidence_binding_rate", 0.0),
        "profile": state.get("profile") or {},
        "route_source": state.get("route_source", ""),
        "urgent": bool(state.get("urgent")),
        "emotion": state.get("emotion"),
    }


@router.post("/chat")
async def chat(req: ChatRequest):
    """PRD POST /api/v1/chat：stream=True 走 SSE，否则一次性返回。"""
    thread_id = req.thread_id or uuid.uuid4().hex
    user_id = (req.user_id or "demo").strip() or "demo"
    trace_id = uuid.uuid4().hex
    inputs = _inputs(req, thread_id, trace_id, user_id)
    config = {"configurable": {"thread_id": thread_id}}
    agent = await get_async_agent()

    if not req.stream:
        state = await agent.ainvoke(inputs, config)
        return _reply_from_state(state, trace_id)

    async def event_gen():
        try:
            async for event in astream_answer(agent, inputs, config):
                yield sse_frame(event)
        except Exception as exc:
            # 流一旦开始，错误也必须走 SSE，不能再抛成普通 JSON
            yield sse_frame({"type": "error", "message": "%s: %s" % (type(exc).__name__, exc)})

    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/profile/{user_id}")
def get_profile(user_id: str):
    """查看某个用户的长期偏好（演示/联调用）。"""
    return {"user_id": user_id, "profile": load_profile(user_id)}


@router.delete("/profile/{user_id}")
def delete_profile(user_id: str):
    """清空长期偏好，便于反复测试。"""
    clear_profile(user_id)
    return {"status": "ok", "user_id": user_id}


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str):
    """按 trace_id 取一次回答的完整轨迹（PRD GET /api/v1/traces/{id}）。"""
    if not cfg.TRACE_LOG.exists():
        raise HTTPException(status_code=404, detail="还没有轨迹记录")
    with cfg.TRACE_LOG.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("trace_id") == trace_id:
                return record
    raise HTTPException(status_code=404, detail="没有找到该 trace_id")


@router.post("/feedback")
def feedback(req: FeedbackRequest):
    """用户点赞/点踩（PRD POST /api/v1/feedback）。"""
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {"trace_id": req.trace_id, "score": req.score, "reason": req.reason}
    with FEEDBACK_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {"status": "ok", "trace_id": req.trace_id}
