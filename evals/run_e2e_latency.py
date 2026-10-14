#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端流式时延：TTFT 门禁 + 完整响应与兜底率观测。

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

from app.rag.generation.answer import NO_EVIDENCE_ANSWER, answer  # noqa: E402
from app.rag.generation.dialogue import DialogueSession, answer_turn  # noqa: E402
from app.rag.retrieval.recall import warmup  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=20, help="单轮查询条数")
ap.add_argument("--gate", type=float, default=2.0, help="TTFT P95 门禁（秒）")
ap.add_argument("--max-fallback-rate", type=float, default=0.05, help="最大生成兜底率")
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
    r = answer(it["query"], city=city, category=cat,
               entity=it.get("entity") if a.mode == "filter" else None)
    rows.append({
        "query": it["query"], "total_ms": int((time.perf_counter() - t) * 1000),
        "retrieval_ms": r["retrieval_latency_ms"],
        "ttft_ms": (r.get("llm") or {}).get("ttft_ms"),
        "llm_ms": (r.get("llm") or {}).get("latency_ms"),
        "no_evidence": r["no_evidence"], "answer_chars": len(r["answer"] or ""),
        "rerank_ms": r["timings"].get("rerank_ms"),
        "attempts": (r.get("llm") or {}).get("attempts"),
        "retried": bool((r.get("llm") or {}).get("retried")),
        "fallback": r.get("fallback"),
        "refused": r.get("answer") == NO_EVIDENCE_ANSWER,
    })

# 一组多轮：追问依赖历史（"那美食呢" / "开放时间是什么" / "那边住宿贵吗"）
MULTI_TURNS = ["成都武侯祠怎么样？", "它的开放时间是什么？", "门票多少钱？",
               "杭州西湖怎么玩？", "那里的开放时间呢？"]
session = DialogueSession()
mt = []
for q in MULTI_TURNS:
    t = time.perf_counter()
    r = answer_turn(q, session)
    mt.append({
        "query": q, "rewritten": r["rewritten_query"], "slots": r["slots"],
        "total_ms": int((time.perf_counter() - t) * 1000),
        "rewrite_ms": r["dialogue_ms"]["rewrite"], "retrieval_ms": r["retrieval_latency_ms"],
        "ttft_ms": (r.get("llm") or {}).get("ttft_ms"),
        "llm_ms": (r.get("llm") or {}).get("latency_ms"), "answer_chars": len(r["answer"] or ""),
        "fallback": r.get("fallback"),
        "refused": r.get("answer") == NO_EVIDENCE_ANSWER,
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
fb = [x for x in rows if x.get("fallback")]
if fb:
    print("   ⚠ 生成兜底 %d/%d：这些回答是相关原文抽取，而非模型最终答案；"
          "具体原因见报告中的 fallback 字段" % (len(fb), len(rows)))
print()
print("== 多轮 %d 轮 ==" % len(mt))
for x in mt:
    print("   %-14s -> %-22s %5d ms (改写 %4d | 检索 %4d | 生成 %5s) 槽位=%s" % (
        x["query"][:12], (x["rewritten"] or "")[:20], x["total_ms"], x["rewrite_ms"],
        x["retrieval_ms"], x["llm_ms"], x["slots"]))
print()
fb_all = [x for x in rows + mt if x.get("fallback")]
print("== 端到端合计 %d 次（其中生成兜底 %d 次）==" % (len(all_lat), len(fb_all)))
print("   均值 %d ms | p50 %d ms | p95 %d ms | max %d ms" % (
    statistics.mean(all_lat), p50, p95, max(all_lat)))
slow = sorted(rows + mt, key=lambda x: -x["total_ms"])[:5]
print("   最慢 5 条：")
for x in slow:
    print("     %5d ms | %s" % (x["total_ms"], x["query"][:40]))

# 多轮正确性断言：追问必须解析到"最近一轮"的地点，且每轮都要有带引用的回答
MT_CHECKS = [(1, "成都", "武侯祠", "它的开放时间是什么？"),
             (2, "成都", "武侯祠", "门票多少钱？"),
             (3, "杭州", "西湖", "杭州西湖怎么玩？"),
             (4, "杭州", "西湖", "那里的开放时间呢？")]
mt_ok = True
for idx, want_city, want_entity, q in MT_CHECKS:
    row = mt[idx]
    city_ok = (row["slots"] or {}).get("city") == want_city
    ans_ok = row["answer_chars"] > 0
    # “杭州西湖怎么玩”必须基于已检索证据作答，非空拒答文案不能冒充多轮成功。
    answer_ok = ans_ok and not row.get("refused")
    entity_ok = (row["slots"] or {}).get("entity") == want_entity
    if not (city_ok and entity_ok and answer_ok):
        mt_ok = False
    print("   多轮校验 %-14s 城市=%-4s(期望 %s) 实体=%-8s(期望 %s) %s | 回答 %d 字 %s" % (
        q[:12], (row["slots"] or {}).get("city"), want_city,
        (row["slots"] or {}).get("entity"), want_entity,
        "OK" if city_ok and entity_ok else "FAIL", row["answer_chars"], "OK" if answer_ok else "FAIL"))
print("%s 多轮跨轮指代（槽位继承 + 改写）" % ("PASS" if mt_ok else "FAIL"))
print()

gate = int(a.gate * 1000)
ttfts = sorted(x["ttft_ms"] for x in rows + mt if x.get("ttft_ms") is not None)
ttft_p50 = ttfts[len(ttfts) // 2] if ttfts else None
ttft_p95 = ttfts[max(int(len(ttfts) * 0.95) - 1, 0)] if ttfts else None
fallback_rate = len(fb_all) / max(len(rows) + len(mt), 1)
ttft_ok = ttft_p95 is not None and ttft_p95 <= gate
fallback_ok = fallback_rate <= a.max_fallback_rate
ok = ttft_ok and fallback_ok and mt_ok
print()
print("%s TTFT P95 %s ms <= %d ms | 完整响应 P95 %d ms（仅观测）" % (
    "PASS" if ttft_ok else "FAIL", ttft_p95, gate, p95))
print("%s 兜底率 %.1f%% <= %.1f%%" % (
    "PASS" if fallback_ok else "FAIL", fallback_rate * 100, a.max_fallback_rate * 100))
print("%s 总门禁（TTFT + 兜底率 + 多轮质量）" % ("PASS" if ok else "FAIL"))

report = {"ttft_gate_ms": gate, "ttft_p95_ms": ttft_p95, "ttft_p50_ms": ttft_p50,
          "completion_p95_ms": p95, "completion_p50_ms": p50,
          "mean_ms": int(statistics.mean(all_lat)), "warmup_ms": warmup_ms,
          "fallback_rate": round(fallback_rate, 4), "fallback_gate": a.max_fallback_rate,
          "ttft_pass": ttft_ok, "fallback_pass": fallback_ok,
          "single_turn": rows, "multi_turn": mt, "pass": ok, "multiturn_pass": mt_ok}
(ROOT / "evals" / "e2e_latency.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写出 %s" % (ROOT / "evals" / "e2e_latency.json"))
sys.exit(0 if ok else 1)
