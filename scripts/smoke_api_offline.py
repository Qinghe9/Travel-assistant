#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""API 离线冒烟：不联网、不需要密钥，验证 FastAPI + SSE 事件序列。

用法：
    python scripts/smoke_api_offline.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 强制离线：必须在导入 app.* 之前清密钥（app.rag.config 用 setdefault，不会回填）
for _key in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "RAG_EMBED_API_KEY", "RAG_SF_API_KEY",
             "OPENTRIPMAP_API_KEY", "ORS_API_KEY", "TRAVELPORT_CREDENTIALS", "AMAP_API_KEY",
             "JEV_APP_KEY"):
    os.environ[_key] = ""
os.environ["RAG_EMBED_PROVIDER"] = "local"
os.environ["AGENT_MEMORY"] = "sqlite"
os.environ["AGENT_DB_PATH"] = str(Path(tempfile.gettempdir()) / "agent_api_smoke.db")
os.environ["AGENT_TRACE_LOG"] = str(Path(tempfile.gettempdir()) / "agent_api_smoke_traces.jsonl")
os.environ["AGENT_PROFILE_DB"] = str(Path(tempfile.gettempdir()) / "agent_api_smoke_profile.db")

for _p in (Path(os.environ["AGENT_DB_PATH"]), Path(os.environ["AGENT_TRACE_LOG"]),
           Path(os.environ["AGENT_PROFILE_DB"])):
    try:
        _p.unlink()
    except FileNotFoundError:
        pass

from fastapi.testclient import TestClient  # noqa: E402

from app.agent import nodes, retrieval  # noqa: E402
from app.api.main import app  # noqa: E402

FAIL = []
TOTAL = [0]
SID = "abcdef123456"


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print("%s %-44s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def fake_evidence(query, city=None, category=None, entity=None, top_k=None):
    meta = {"doc_title": "西安", "city": "西安", "province": "陕西",
            "source_url": "https://example.org/xian", "source_type": "public",
            "updated_at": "2026-10-01"}
    return {
        "query": query,
        "chunks": [{"chunk_id": "wx#0000", "source_id": SID,
                    "text": "西安城墙是中国现存规模最大的古城墙，可以租自行车环城。",
                    "score_fused": 0.5, "metadata": meta}],
        "citations": [{"source_id": SID, "title": "西安", "url": meta["source_url"]}],
        "no_evidence": False, "degraded": False, "empty": False,
        "retrieval_latency_ms": 3, "max_fused_score": 0.05, "max_vector_sim": 0.7,
        "timings": {}, "index_version": "smoke",
    }


retrieval.build_evidence = fake_evidence
nodes.retrieval.build_evidence = fake_evidence


def parse_sse(text):
    events = []
    for block in (text or "").split("\n\n"):
        for line in block.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                raw = line[5:].strip()
                if raw:
                    events.append(json.loads(raw))
    return events


print("=" * 64)
print("API 离线冒烟（不联网、不需要密钥）")
print("=" * 64)

with TestClient(app) as client:
    r = client.get("/health")
    check("GET /health 可访问", r.status_code == 200, str(r.status_code))
    check("health 报告依赖状态", "deps" in (r.json() or {}), str(r.json())[:60])

    r = client.get("/")
    check("首页 / 可访问", r.status_code == 200 and "Agent API" in r.text, str(r.status_code))
    r = client.get("/demo")
    check("调试台 /demo 可访问",
          r.status_code == 200 and "开启新对话" in r.text and "给助手发送消息" in r.text,
          str(r.status_code))
    r = client.get("/static/coast.jpg")
    check("氛围底图可访问", r.status_code == 200 and "image" in r.headers.get("content-type", ""),
          r.headers.get("content-type", ""))

    # 非流式
    r = client.post("/api/v1/chat", json={"message": "帮我规划3天西安行程", "stream": False})
    body = r.json() if r.status_code == 200 else {}
    check("POST /api/v1/chat 非流式", r.status_code == 200, str(r.status_code))
    check("返回意图与答案", body.get("intent") == "INT-02" and bool(body.get("answer")),
          str(body.get("intent")))
    check("答案带引用", SID in (body.get("answer") or ""), (body.get("answer") or "")[:40])
    trace_id = body.get("trace_id") or ""
    check("返回 trace_id", bool(trace_id), trace_id)

    # 异步 SQLite 多轮：同 thread_id 必须继承槽位。
    # 这条用例专门抓"同步 SqliteSaver 配异步图"的 NotImplementedError。
    client.post("/api/v1/chat", json={"message": "帮我规划3天西安行程",
                                      "thread_id": "t-mem", "stream": False})
    r = client.post("/api/v1/chat", json={"message": "第二天改成亲子活动",
                                          "thread_id": "t-mem", "stream": False})
    slots = (r.json() or {}).get("slots") or {}
    check("异步 SQLite 多轮记忆", slots.get("destination") == "西安" and slots.get("days") == 3,
          str(slots))

    # SSE
    r = client.post("/api/v1/chat", json={"message": "成都有什么好玩的", "stream": True})
    check("SSE 内容类型", r.headers.get("content-type", "").startswith("text/event-stream"),
          r.headers.get("content-type", ""))
    events = parse_sse(r.text)
    types = [e.get("type") for e in events]
    check("SSE 事件序列", types[:1] == ["meta"] and types[-1] == "final", str(types))
    check("包含 step 事件", "step" in types, str(types[:8]))
    check("step 带耗时", any("latency_ms" in e for e in events if e.get("type") == "step"), "")
    final = next((e for e in events if e.get("type") == "final"), {})
    check("final 带答案与引用", bool(final.get("answer")) and bool(final.get("citations")),
          (final.get("answer") or "")[:40])
    check("final 带 trace_id", bool(final.get("trace_id")), final.get("trace_id", ""))

    # 轨迹查询
    tid = final.get("trace_id") or ""
    r = client.get("/api/v1/traces/%s" % tid)
    check("GET /traces/{id} 可查", r.status_code == 200 and r.json().get("trace_id") == tid,
          str(r.status_code))

    # 反馈
    r = client.post("/api/v1/feedback", json={"trace_id": tid, "score": 1, "reason": "ok"})
    check("POST /feedback 可写", r.status_code == 200 and r.json().get("status") == "ok",
          str(r.status_code))

    # 长期记忆：同一 user、换 thread 仍保留偏好；不同 user 互相隔离
    client.delete("/api/v1/profile/u-long")
    client.delete("/api/v1/profile/u-other")
    client.post("/api/v1/chat", json={
        "message": "我喜欢历史文化，2人预算3000", "user_id": "u-long",
        "thread_id": "long-a", "stream": False})
    r = client.post("/api/v1/chat", json={
        "message": "帮我规划3天西安行程", "user_id": "u-long",
        "thread_id": "long-b", "stream": False})
    body = r.json() or {}
    prefs = (body.get("profile") or {}).get("preferences") or []
    slot_prefs = (body.get("slots") or {}).get("preferences") or []
    check("跨 thread 保留长期偏好", "历史文化" in prefs and "历史文化" in slot_prefs,
          str(prefs))
    r2 = client.post("/api/v1/chat", json={
        "message": "帮我规划3天西安行程", "user_id": "u-other",
        "thread_id": "other-a", "stream": False})
    other = (r2.json() or {}).get("profile") or {}
    check("长期偏好按用户隔离", not (other.get("preferences") or []), str(other))
    r3 = client.get("/api/v1/profile/u-long")
    check("GET /profile 可查", "历史文化" in ((r3.json() or {}).get("profile") or {}).get("preferences", []),
          str(r3.status_code))

    # 假模型：验证 token 事件真的能从节点里流出来
    from app.agent import llm as agent_llm
    real_available, real_stream, real_chat_json = (
        agent_llm.available, agent_llm.chat_stream, agent_llm.chat_json)

    def fake_stream(messages, **kwargs):
        on_token = kwargs.get("on_token")
        text = "西安城墙可以租自行车环城。[%s]" % SID
        for ch in text:
            if on_token:
                on_token(ch)
        return {"text": text, "model": "stub", "usage": {}, "ttft_ms": 10,
                "latency_ms": 20, "attempts": 1}

    agent_llm.available = lambda: True
    agent_llm.chat_json = lambda messages, schema, **kw: (None, "stub: 走规则分类")
    agent_llm.chat_stream = fake_stream
    try:
        r = client.post("/api/v1/chat", json={"message": "西安城墙怎么玩", "stream": True})
        events = parse_sse(r.text)
        types = [e.get("type") for e in events]
        tokens = "".join(e.get("content", "") for e in events if e.get("type") == "token")
        check("模型 token 可流式输出", "token" in types, str(types[-6:]))
        check("token 拼接成完整答案", SID in tokens, tokens[:50])
    finally:
        agent_llm.available, agent_llm.chat_stream, agent_llm.chat_json = (
            real_available, real_stream, real_chat_json)

print("-" * 64)
print("通过 %d 项，失败 %d 项" % (TOTAL[0] - len(FAIL), len(FAIL)))
if FAIL:
    print("失败项：", "、".join(FAIL))
sys.exit(1 if FAIL else 0)
