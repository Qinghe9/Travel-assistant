#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RAG 检索评测：Recall@3 / MRR / nDCG@3 / 无关知识引入 / 证据绑定率 / 时延。

用法：
    python evals/run_rag_eval.py --mode both
    python evals/run_rag_eval.py --mode filter --limit 20
    python evals/run_rag_eval.py --mode both --save-baseline
"""

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402
from app.rag.retrieval.evidence import build_evidence, source_id  # noqa: E402
from app.rag.retrieval.fuse import search_with_score  # noqa: E402
from app.rag.retrieval import recall as recall_mod
from app.rag.retrieval.recall import warmup  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--mode", default="both", choices=["open", "filter", "both"],
                help="open=不传过滤；filter=传 city/category（产品实际行为）")
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--no-gate", action="store_true", help="只出报告，不做门禁判定")
ap.add_argument("--gate-modes", default="filter",
                help="对哪些模式做门禁。默认 filter：Agent 按 FR-02 一定会传 city 过滤，"
                     "open 模式只作为「万一没传过滤」的诊断，不作为验收门禁")
ap.add_argument("--save-baseline", action="store_true")
a = ap.parse_args()

EVALS = ROOT / "evals"
K = 3

# 预热：构建进程内热索引（向量矩阵 + 正文元数据 + 词法倒排），并初始化 jieba/BM25/Embedder。
# 不做这一步，评测跑的是 Chroma 回退路径，量出来的时延不代表线上配置。
try:
    warmup()
except Exception as _exc:  # noqa: BLE001
    print("预热失败（继续用回退路径）：%s" % _exc)

items = [json.loads(l) for l in (EVALS / "rag_eval.jsonl").open(encoding="utf-8")]
if a.limit:
    items = items[: a.limit]


# 同源校验：评测集里的 relevant_source_ids 必须存在于当前 chunks.jsonl。
# 重新切分/重建索引后 source_id 会变，旧评测集必须重新生成，否则 Recall 会假性暴跌。
def _check_same_source():
    cur = set()
    with cfg.CHUNKS_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            c = json.loads(line)
            cur.add(hashlib.sha1(("%s#%s" % (c.get("source_url", ""), c["chunk_id"])).encode("utf-8")).hexdigest()[:12])
    want = set()
    for it in items:
        want.update(it.get("relevant_source_ids") or [])
    hit = len(want & cur)
    rate = hit / max(len(want), 1)
    print("评测集同源校验：relevant_source_ids %d 个，能对上当前 chunk 的 %d 个（%.1f%%）" % (len(want), hit, 100 * rate))
    if rate < 0.5:
        raise SystemExit(
            "评测集与当前索引不同源（命中率 %.1f%%）。重新切分后 source_id 会变，"
            "请先运行：python scripts/build_rag_eval.py" % (100 * rate))


_check_same_source()


def ndcg(hit_sids, relevant, k=K):
    dcg = sum(1.0 / __import__("math").log2(i + 2) for i, s in enumerate(hit_sids[:k]) if s in relevant)
    idcg = sum(1.0 / __import__("math").log2(i + 2) for i in range(min(k, len(relevant))))
    return dcg / idcg if idcg else 0.0


def run_mode(mode):
    recall_mod.reset_timing()
    rs, mrrs, ndcgs, lat = [], [], [], []
    irrelevant = returned = empty = 0
    bound = off_target = 0
    misses = []
    for it in items:
        city = it.get("city") if mode == "filter" else None
        cat = it.get("category") if mode == "filter" else None
        t0 = time.perf_counter()
        hits, _ = search_with_score(it["query"], city=city, category=cat, top_k=K)
        dt = int((time.perf_counter() - t0) * 1000)
        lat.append(dt)
        relevant = set(it.get("relevant_source_ids") or [])
        negatives = set(it.get("hard_negatives") or [])
        sids = [source_id((h["metadata"] or {}).get("source_url", ""), h["chunk_id"]) for h in hits]
        returned += len(sids)
        if not hits:
            empty += 1
        for s in sids:
            if s:
                bound += 1                      # 有 source_id 即算绑定成功（可被引用）
            if s in negatives:
                irrelevant += 1                 # 命中了明确负例 = 引入无关知识
            if s not in relevant:
                off_target += 1
        if relevant:
            # 文档级相关性：Top-K 里只要命中任意一条相关 chunk 即算召回成功
            rs.append(1.0 if any(s in relevant for s in sids) else 0.0)
            rank = next((i + 1 for i, s in enumerate(sids) if s in relevant), 0)
            mrrs.append(1.0 / rank if rank else 0.0)
            ndcgs.append(ndcg(sids, relevant))
            if rank == 0 and len(misses) < 5:
                misses.append((it["query"], [(h["chunk_id"][-4:], (h["metadata"] or {}).get("city"), round(h["score_fused"], 5)) for h in hits]))
        else:
            empty += 0
    n = len([1 for it in items if it.get("relevant_source_ids")])
    lat_sorted = sorted(lat)
    p95 = lat_sorted[max(int(len(lat_sorted) * 0.95) - 1, 0)]
    return {
        "queries": len(items), "scored": n,
        "recall_at_k": round(statistics.mean(rs), 4) if rs else 0.0,
        "mrr": round(statistics.mean(mrrs), 4) if mrrs else 0.0,
        "ndcg_at_k": round(statistics.mean(ndcgs), 4) if ndcgs else 0.0,
        "irrelevant_knowledge": irrelevant,
        "irrelevant_ratio": round(irrelevant / max(returned, 1), 4),
        "off_target_chunks": off_target,
        "evidence_binding_rate": round(bound / max(returned, 1), 4),
        "empty_evidence": empty,
        "latency_ms_mean": int(statistics.mean(lat)) if lat else 0,
        "latency_ms_p95": int(p95),
        # 分阶段耗时（每查询均值），用于定位时延花在哪一段
        "stage_ms": {k: round(v / max(len(items), 1), 2) for k, v in recall_mod.TIMING.items()},
        "misses": misses,
    }


# 边界集里只有这两类考的是检索：知识库可得（必须有证据）/ 两者皆无（必须拒答）。
# 其余是工具故障注入场景（天气 429、铁路超时…），考的是工具层，本评测跳过。
BOUNDARY_RETRIEVAL = {"only_knowledge_available": "knowledge_only", "neither_available": "neither"}


def run_boundary():
    path = EVALS / "boundary_eval.jsonl"
    if not path.exists():
        return {}
    rows = [json.loads(l) for l in path.open(encoding="utf-8")]
    skipped = len(rows)
    ko, ne = [], []
    for r in rows:
        kind = BOUNDARY_RETRIEVAL.get(r.get("scenario") or r.get("kind"))
        if kind == "knowledge_only":
            ko.append(r)
        elif kind == "neither":
            ne.append(r)
    skipped -= len(ko) + len(ne)
    ko_ok = sum(1 for r in ko if not build_evidence(r["query"])["no_evidence"])
    ne_ok = sum(1 for r in ne if build_evidence(r["query"])["no_evidence"])
    return {"knowledge_only": {"total": len(ko), "with_evidence": ko_ok,
                               "evidence_rate": round(ko_ok / max(len(ko), 1), 4)},
            "neither": {"total": len(ne), "refused": ne_ok,
                        "refusal_rate": round(ne_ok / max(len(ne), 1), 4)},
            "skipped_tool_scenarios": skipped}


modes = ["open", "filter"] if a.mode == "both" else [a.mode]
report = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "k": K, "results": {}}
for m in modes:
    r = run_mode(m)
    report["results"][m] = r
    print("== 模式 %s ==" % m)
    print("  查询 %d | Recall@3 %.4f | MRR %.4f | nDCG@3 %.4f" % (
        r["queries"], r["recall_at_k"], r["mrr"], r["ndcg_at_k"]))
    print("  无关知识 %d 条（占返回 %.2f%%）| 证据绑定率 %.4f | 空证据 %d 条" % (
        r["irrelevant_knowledge"], 100 * r["irrelevant_ratio"], r["evidence_binding_rate"], r["empty_evidence"]))
    print("  未落在相关集的返回 chunk %d 条（信息性指标，不计入门禁）" % r["off_target_chunks"])
    print("  时延 均值 %dms | P95 %dms" % (r["latency_ms_mean"], r["latency_ms_p95"]))
    print("  分段(ms/查询)： " + " | ".join("%s %.1f" % (k.replace("_ms", ""), v) for k, v in r["stage_ms"].items()))
    for q, top in r["misses"]:
        print("    未命中: %s | top=%s" % (q, top))

b = run_boundary()
report["boundary"] = b
if b:
    print("== 边界集 ==")
    print("  只在知识库可得: %d/%d 有证据（%.2f）" % (b["knowledge_only"]["with_evidence"],
                                                b["knowledge_only"]["total"], b["knowledge_only"]["evidence_rate"]))
    print("  两者皆无     : %d/%d 拒答（%.2f）" % (b["neither"]["refused"],
                                              b["neither"]["total"], b["neither"]["refusal_rate"]))
    if b.get("skipped_tool_scenarios"):
        print("  跳过工具故障注入场景 %d 条（考的是工具层，不属检索评测）" % b["skipped_tool_scenarios"])

(EVALS / "rag_metrics.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写出 %s" % (EVALS / "rag_metrics.json"))

if a.no_gate:
    sys.exit(0)

gates = []
gate_modes = [m.strip() for m in a.gate_modes.split(",") if m.strip()]
for m in [x for x in modes if x in gate_modes]:
    r = report["results"][m]
    gates.append(("Recall@3 >= 0.95 [%s]" % m, r["recall_at_k"] >= 0.95, r["recall_at_k"]))
    gates.append(("MRR >= 0.90 [%s]" % m, r["mrr"] >= 0.90, r["mrr"]))
    gates.append(("无关知识 == 0 [%s]" % m, r["irrelevant_knowledge"] == 0, r["irrelevant_knowledge"]))
    # 流程文档：检索阶段 P95 <= 300ms（原先只在文档里写着，没有门禁守着）
    gates.append(("时延 P95 <= 300ms [%s]" % m, r["latency_ms_p95"] <= 300, r["latency_ms_p95"]))
if b:
    gates.append(("无证据拒答率 >= 0.90", b["neither"]["refusal_rate"] >= 0.90, b["neither"]["refusal_rate"]))
    gates.append(("知识库可得问题有证据率 >= 0.90", b["knowledge_only"]["evidence_rate"] >= 0.90,
                  b["knowledge_only"]["evidence_rate"]))

base_path = EVALS / "baseline.json"
if base_path.exists():
    base = json.loads(base_path.read_text(encoding="utf-8"))
    for m in modes:
        if m in base.get("results", {}):
            for key, label in (("recall_at_k", "Recall@3"), ("mrr", "MRR")):
                old = base["results"][m][key]
                new = report["results"][m][key]
                drop = (new - old) * 100
                if drop < -2:
                    gates.append(("相对基线退化 %.1f 个百分点 %s [%s]" % (drop, label, m), False, new))

print("-" * 56)
for name, ok, val in gates:
    print("%s %s" % ("PASS" if ok else "FAIL", name))
if a.save_baseline and all(ok for _, ok, _ in gates):
    base_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入基线 %s" % base_path)
sys.exit(0 if all(ok for _, ok, _ in gates) else 1)
