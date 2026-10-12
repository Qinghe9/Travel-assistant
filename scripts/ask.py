#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端问答：检索 -> 证据 -> DeepSeek 生成 -> 引用后置校验。

用法：
    python scripts/ask.py "成都有哪些必去的景点" --city 成都 --category attraction
    python scripts/ask.py "杭州西湖怎么玩" --city 杭州
    python scripts/ask.py "成都有什么特色美食" --json
    python scripts/ask.py "成都有什么特色美食" --show-evidence
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="旅游规划助手 · RAG 问答")
ap.add_argument("query", help="用户问题")
ap.add_argument("--city", default=None, help="城市过滤（Agent 抽取到的目的地）")
ap.add_argument("--category", default=None, help="类别过滤 attraction/food/hotel/transport/tips")
ap.add_argument("--top-k", type=int, default=3)
ap.add_argument("--json", action="store_true", help="输出完整 JSON")
ap.add_argument("--show-evidence", action="store_true", help="打印注入模型的证据原文")
ap.add_argument("--keep-unbound", action="store_true", help="不剔除未绑定引用的句子（调试用）")
a = ap.parse_args()

from app.rag import config as cfg  # noqa: E402
from app.rag.generation import answer as A  # noqa: E402
from app.rag.retrieval.recall import warmup  # noqa: E402

warmup()   # 服务里只需启动时调一次；命令行每个进程都要付一次冷启动

if a.show_evidence:
    from app.rag.retrieval.evidence import build_evidence
    ev = build_evidence(a.query, city=a.city, category=a.category, top_k=a.top_k)
    print("注入证据 %d 条：" % len(ev["chunks"]))
    for c in ev["chunks"]:
        md = c["metadata"] or {}
        print("  [%s] %s | %s/%s | %s | %d 字" % (
            c["source_id"], md.get("doc_title"), md.get("province"), md.get("city"),
            md.get("category"), len(c["text"])))
    print("-" * 60)

r = A.answer(a.query, city=a.city, category=a.category, top_k=a.top_k, keep_unbound=a.keep_unbound)

if a.json:
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0)

print("问题：%s%s" % (a.query, ("（城市=%s 类别=%s）" % (a.city, a.category)) if (a.city or a.category) else ""))
print("=" * 60)
print(r["answer"])
print("=" * 60)
print("引用（%d 条）：" % len(r["citations"]))
for c in r["citations"]:
    print("  [%s] %s  %s" % (c["source_id"], c["title"] or "-", c["url"]))
info = r.get("llm") or {}
print("-" * 60)
print("检索 %dms（embed %.0f / vector %.0f / bm25 %.0f / fuse %.0f / meta %.0f）| 相似度 %.3f" % (
    r["retrieval_latency_ms"], r["timings"]["embed_ms"], r["timings"]["vector_ms"],
    r["timings"]["bm25_ms"], r["timings"]["fuse_ms"], r["timings"].get("meta_ms", 0),
    r["max_vector_sim"]))
if info.get("error"):
    print("生成失败：%s %s" % (info["error"]["code"], info["error"]["message"]))
else:
    print("生成 %sms | 模型 %s | tokens %s | 重试 %s 次" % (
        info.get("latency_ms"), info.get("model"),
        (info.get("usage") or {}).get("total_tokens"), (info.get("attempts") or 1) - 1))
print("证据绑定率 %.2f | 无证据=%s | 降级=%s | 被剔除句 %d | 未绑定句 %d | 索引 %s" % (
    r.get("evidence_binding_rate", 0), r["no_evidence"], r["degraded"],
    len(r["dropped_sentences"]), len(r["unbound_sentences"]), r["index_version"]))
if r["unbound_sentences"]:
    print("未绑定示例：", r["unbound_sentences"][:2])
