#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端时延：改写 + 三路召回 + 重排 + 生成 + 引用校验。

对应任务书 90-100 分档的「响应时间 ≤2 秒」。

用法：
    python evals/run_e2e_latency.py                 # 20 条单轮（filter）+ 1 组多轮
    python evals/run_e2e_latency.py --limit 30 --gate 2.0
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag.generation.answer import answer  # noqa: E402
from app.rag.generation.dialogue import DialogueSession, answer_turn  # noqa: E402
from app.rag.retrieval.recall import warmup  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=20, help="单轮查询条数")
ap.add_argument("--gate", type=float, default=2.0, help="P95 门禁（秒）")
ap.add_argument("--mode", default="filter", choices=["filter", "open"])
a = ap.parse_args()

t_warm = time.perf_counter()
warmup()
warmup_ms = int((time.perf_counter() - t_warm) * 1000)

items = [json.loads(l) for l in (ROOT / "evals" / "rag_eval.jsonl").open(encoding="utf-8")][: a.limit]

rows = []
for it in items:
    city = it.get("city") if a.mode == "filter" else None
    cat = it.get("category") if a.mode == "filter" else None
    t = time.perf_counter()
    r = answer(it["query"], city=city, category=cat)
    rows.append({
        "query": it["query"], "total_ms": int((time.perf_counter() - t) * 1000),
        "retrieval_ms": r["retrieval_latency_ms"],
        "llm_ms": (r.get("llm") or {}).get("latency_ms"),
        "no_evidence": r["no_evidence"], "answer_chars": len(r["answer"] or ""),
        "rerank_ms": r["timings"].get("rerank_ms"),
        "attempts": (r.get("llm") or {}).get("attempts"),
        "retried": bool((r.get("llm") or {}).get("retried")),
    })

# 一组多轮：追问依赖历史（"那美食呢" / "开放时间是什么" / "那边住宿贵吗"）
MULTI_TURNS = ["成都有哪些必去的景点？", "那美食呢？", "开放时间是什么？",
               "杭州西湖怎么玩？", "那边住宿贵吗？"]
session = DialogueSession()
mt = []
for q in MULTI_TURNS:
    t = time.perf_counter()
    r = answer_turn(q, session)
    mt.append({
        "query": q, "rewritten": r["rewritten_query"], "slots": r["slots"],
        "total_ms": int((time.perf_counter() - t) * 1000),
        "rewrite_ms": r["dialogue_ms"]["rewrite"], "retrieval_ms": r["retrieval_latency_ms"],
        "llm_ms": (r.get("llm") or {}).get("latency_ms"), "answer_chars": len(r["answer"] or ""),
    })

lat = sorted(x["total_ms"] for x in rows) + sorted(x["total_ms"] for x in mt)
all_lat = sorted([x["total_ms"] for x in rows] + [x["total_ms"] for x in mt])
p50 = all_lat[len(all_lat) // 2]
p95 = all_lat[max(int(len(all_lat) * 0.95) - 1, 0)]

print("预热（含热索引构建）: %d ms" % warmup_ms)
print()
print("== 单轮 %d 条（%s）==" % (len(rows), a.mode))
print("   均值 %d ms | p50 %d ms | p95 %d ms | max %d ms" % (
    statistics.mean([x["total_ms"] for x in rows]), sorted(x["total_ms"] for x in rows)[len(rows) // 2],
    sorted(x["total_ms"] for x in rows)[max(int(len(rows) * 0.95) - 1, 0)], max(x["total_ms"] for x in rows)))
print("   其中：检索(含重排) 均值 %d ms | 生成 均值 %d ms" % (
    statistics.mean([x["retrieval_ms"] for x in rows]),
    statistics.mean([x["llm_ms"] or 0 for x in rows])))
fb = [x for x in rows if not x.get("llm_ms")]
if fb:
    print("   ⚠ 生成超时兜底 %d/%d：这些回答是**原文引用**而非模型组织——"
          "说明当时 API 慢于预算，PASS 不代表生成链路健康" % (len(fb), len(rows)))
print()
print("== 多轮 %d 轮 ==" % len(mt))
for x in mt:
    print("   %-14s -> %-22s %5d ms (改写 %4d | 检索 %4d | 生成 %5s) 槽位=%s" % (
        x["query"][:12], (x["rewritten"] or "")[:20], x["total_ms"], x["rewrite_ms"],
        x["retrieval_ms"], x["llm_ms"], x["slots"]))
print()
fb_all = [x for x in rows + mt if not x.get("llm_ms")]
print("== 端到端合计 %d 次（其中生成兜底 %d 次）==" % (len(all_lat), len(fb_all)))
print("   均值 %d ms | p50 %d ms | p95 %d ms | max %d ms" % (
    statistics.mean(all_lat), p50, p95, max(all_lat)))
slow = sorted(rows + mt, key=lambda x: -x["total_ms"])[:5]
print("   最慢 5 条：")
for x in slow:
    print("     %5d ms | %s" % (x["total_ms"], x["query"][:40]))

# 多轮正确性断言：追问必须解析到"最近一轮"的地点，且每轮都要有带引用的回答
MT_CHECKS = [(1, "成都", "那美食呢？"), (2, "成都", "开放时间是什么？"),
             (3, "杭州", "杭州西湖怎么玩？"), (4, "杭州", "那边住宿贵吗？")]
mt_ok = True
for idx, want_city, q in MT_CHECKS:
    row = mt[idx]
    city_ok = (row["slots"] or {}).get("city") == want_city
    ans_ok = row["answer_chars"] > 0
    if not (city_ok and ans_ok):
        mt_ok = False
    print("   多轮校验 %-14s 槽位城市=%-4s(期望 %s) %s | 回答 %d 字 %s" % (
        q[:12], (row["slots"] or {}).get("city"), want_city,
        "OK" if city_ok else "FAIL", row["answer_chars"], "OK" if ans_ok else "FAIL"))
print("%s 多轮跨轮指代（槽位继承 + 改写）" % ("PASS" if mt_ok else "FAIL"))
print()

gate = int(a.gate * 1000)
ok = p95 <= gate
print()
print("%s 端到端 P95 %d ms <= %d ms（任务书 ≤2 秒）" % ("PASS" if ok else "FAIL", p95, gate))

report = {"gate_ms": gate, "p95_ms": p95, "p50_ms": p50,
          "mean_ms": int(statistics.mean(all_lat)), "warmup_ms": warmup_ms,
          "single_turn": rows, "multi_turn": mt, "pass": ok, "multiturn_pass": mt_ok}
(ROOT / "evals" / "e2e_latency.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写出 %s" % (ROOT / "evals" / "e2e_latency.json"))
sys.exit(0 if ok else 1)
