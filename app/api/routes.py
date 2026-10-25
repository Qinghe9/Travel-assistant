# -*- coding: utf-8 -*-
"""业务路由：对话、轨迹、反馈。"""

from __future__ import annotations

import json
import hashlib
import csv
import io
import sqlite3
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.agent import config as cfg
from app.agent import slots as agent_slots
from app.agent.graph import get_agent, get_async_agent
from app.agent.store import append_turn, clear_profile, load_history, load_profile, save_profile
from app.agent.sqlite_utils import tune_sqlite
from app.agent.streaming import astream_answer
from app.api.sse import SSE_HEADERS, sse_frame
from app.models.schemas import ChatRequest, FeedbackRequest, now_iso

router = APIRouter()

FEEDBACK_LOG = cfg.DATA / "agent" / "feedback.jsonl"
FEEDBACK_DB = cfg.DATA / "agent" / "feedback.db"


def _feedback_connection():
    FEEDBACK_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(FEEDBACK_DB), check_same_thread=False)
    tune_sqlite(conn)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS feedback ("
        "trace_id TEXT, score INTEGER, reason TEXT, created_at TEXT)")
    conn.commit()
    return conn


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
        "normalized_query": state.get("normalized_query", ""),
        "intent": state.get("intent", ""),
        "intents": state.get("intents") or [],
        "tasks": state.get("tasks") or [],
        "confidence": state.get("confidence", 0.0),
        "slots": state.get("slots") or {},
        "destination_source": state.get("destination_source", ""),
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
    return {
        "user_id": user_id,
        "profile": load_profile(user_id),
        "memory_turns": len(load_history(user_id)),
    }


@router.get("/profile/{user_id}/memory")
def get_memory(user_id: str):
    """导出该用户保存的全部跨会话对话记录（演示/调试用）。"""
    rows = load_history(user_id)
    return {"user_id": user_id, "count": len(rows), "turns": rows}


@router.post("/memory/import")
def import_local_memory(payload: dict):
    """把浏览器 localStorage 中已有的会话补录为跨会话记忆。

    只导入用户与助手最终文本、引用、trace/thread 和时间；幂等键优先使用
    trace_id，没有 trace_id 时使用稳定哈希，重复同步不会制造重复记录。
    """
    user_id = str(payload.get("user_id") or "demo").strip() or "demo"
    imported = 0
    for conv in payload.get("conversations") or []:
        thread_id = str((conv or {}).get("threadId") or (conv or {}).get("id") or "")
        pending_user = None
        for message in (conv or {}).get("messages") or []:
            role = (message or {}).get("role")
            content = str((message or {}).get("content") or "").strip()
            if role == "user":
                pending_user = message
                continue
            if role != "assistant" or pending_user is None or not content:
                continue
            query = str(pending_user.get("content") or "").strip()
            trace_id = str(message.get("traceId") or "").strip()
            if not trace_id:
                raw = "%s|%s|%s|%s" % (user_id, thread_id, query, content)
                trace_id = "local-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]
            destination = agent_slots.detect_city(query)
            created_at = message.get("at") or pending_user.get("at")
            if isinstance(created_at, (int, float)):
                created_at = datetime.fromtimestamp(created_at / 1000.0).astimezone().isoformat(
                    timespec="seconds")
            inserted = append_turn(user_id, {
                "trace_id": trace_id,
                "thread_id": thread_id,
                "query": query,
                "answer": content,
                "intent": "",
                "intents": [],
                "tasks": [],
                "slots": {"destination": destination} if destination else {},
                "route_source": "local_import",
                "citations": list(message.get("citations") or []),
                "tool_results": [],
                "created_at": created_at,
                "imported_from": "localStorage",
            }, overwrite=False)
            if inserted:
                imported += 1
                if destination:
                    save_profile(user_id, {"destination": destination})
            pending_user = None
    return {"status": "ok", "user_id": user_id, "imported": imported,
            "memory_turns": len(load_history(user_id))}


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
    record = {"trace_id": req.trace_id, "score": req.score, "reason": req.reason,
              "created_at": now_iso()}
    with FEEDBACK_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    conn = _feedback_connection()
    try:
        conn.execute(
            "INSERT INTO feedback(trace_id, score, reason, created_at) VALUES (?,?,?,?)",
            (record["trace_id"], record["score"], record["reason"], record["created_at"]))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "trace_id": req.trace_id}


@router.get("/feedback/export")
def export_feedback(start: str = None, end: str = None):
    """按日期导出反馈 CSV；日期格式 YYYY-MM-DD，范围包含首尾。"""
    for value in (start, end):
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="日期必须是 YYYY-MM-DD")
    conn = _feedback_connection()
    try:
        sql = "SELECT trace_id, score, reason, created_at FROM feedback WHERE 1=1"
        params = []
        if start:
            sql += " AND substr(created_at,1,10) >= ?"
            params.append(start)
        if end:
            sql += " AND substr(created_at,1,10) <= ?"
            params.append(end)
        sql += " ORDER BY created_at"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(("trace_id", "score", "reason", "created_at"))
    writer.writerows(rows)
    return StreamingResponse(
        iter([buffer.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=feedback.csv"})
