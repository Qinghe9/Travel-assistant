#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""索引验收：Manifest 字段、向量数一致、抽样命中、过滤正确、域内外可分、无证据标记。

用法：
    python scripts/verify_index.py
    python scripts/verify_index.py --twice      # 额外做「连续两次构建一致」检查
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser()
ap.add_argument("--twice", action="store_true", help="连续构建两次并比对 chunk 数与维度")
ap.add_argument("--queries", nargs="*", default=None, help="自定义抽样查询")
a = ap.parse_args()

from app.rag import config as cfg  # noqa: E402
from app.rag.retrieval.fuse import search  # noqa: E402

FAIL, PASS = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("%s %-38s %s" % ("PASS" if ok else "FAIL", name, detail))


print("=" * 60)
print("索引验收")
print("=" * 60)

need = ["index_version", "data_snapshot_date", "doc_count", "chunk_count", "embedding_model",
        "chunk_size", "overlap", "bm25_params", "built_at", "git_commit"]
mf = json.loads(cfg.MANIFEST_FILE.read_text(encoding="utf-8")) if cfg.MANIFEST_FILE.exists() else {}
missing = [k for k in need if k not in mf or mf[k] in (None, "")]
check("IndexManifest 字段齐全", not missing, "缺: %s" % missing if missing else
      "chunk_count=%s dim=%s model=%s" % (mf.get("chunk_count"), mf.get("embedding_dim"),
                                          mf.get("embedding_model")))
check("构建未降级", not mf.get("degraded"), "degraded=%s" % mf.get("degraded"))

from app.rag.indexing.build import get_client  # noqa: E402
col = get_client().get_collection(cfg.COLLECTION)
n = col.count()
check("Chroma 向量数 == chunk_count", n == mf.get("chunk_count"), "%s vs %s" % (n, mf.get("chunk_count")))

queries = a.queries or ["成都有哪些必去的景点", "杭州西湖怎么玩", "重庆有什么特色美食",
                        "西安到北京怎么坐高铁", "哈尔滨冬天要注意什么"]
print("-" * 60)
hit = 0
for q in queries:
    hits = search(q, top_k=3)
    if hits:
        hit += 1
        m0 = hits[0]["metadata"]
        print("  %-22s -> %d 条 | 首选: %s（%s/%s, %.4f）" % (
            q, len(hits), m0.get("doc_title") or m0.get("parent_doc_id"),
            m0.get("province"), m0.get("city"), hits[0]["score_fused"]))
    else:
        print("  %-22s -> 空结果" % q)
check("抽样查询有命中", hit == len(queries), "%d/%d" % (hit, len(queries)))

hits = search("有什么好玩的景点", city="成都", top_k=5)
bad = [h for h in hits if (h["metadata"] or {}).get("city") != "成都"]
check("city 过滤下推正确", bool(hits) and not bad, "%d 条，越界 %d 条" % (len(hits), len(bad)))

hits = search("推荐几个景点", category="attraction", top_k=5)
bad = [h for h in hits if (h["metadata"] or {}).get("category") != "attraction"]
check("category 过滤正确", bool(hits) and not bad, "%d 条，越界 %d 条" % (len(hits), len(bad)))

IN_DOMAIN = ["成都有哪些必去的景点", "杭州西湖怎么玩", "重庆有什么特色美食",
             "西安到北京怎么坐高铁", "哈尔滨冬天要注意什么"]
OUT_DOMAIN = ["量子纠缠退相干实验的低温恒温器型号", "如何用 Python 实现快速排序",
              "股票期权定价的 Black-Scholes 公式", "糖尿病的一线用药方案", "C 语言指针的内存对齐"]
in_scores, out_scores = [], []
for q in IN_DOMAIN:
    h = search(q, top_k=3)
    in_scores.append(max([x["score_fused"] for x in h], default=0.0))
for q in OUT_DOMAIN:
    h = search(q, top_k=3)
    out_scores.append(max([x["score_fused"] for x in h], default=0.0))
print("-" * 60)
print("  域内查询最高融合分: %.5f ~ %.5f（均值 %.5f）" % (
    min(in_scores), max(in_scores), sum(in_scores) / len(in_scores)))
print("  域外查询最高融合分: %.5f ~ %.5f（均值 %.5f）" % (
    min(out_scores), max(out_scores), sum(out_scores) / len(out_scores)))
gap_ok = min(in_scores) > max(out_scores)
check("域内/域外融合分可分离", gap_ok,
      "建议阈值 %.5f" % ((min(in_scores) + max(out_scores)) / 2) if gap_ok
      else "区间重叠，需调阈值或加 TopK/权重（当前 MIN_FUSED_SCORE=%s）" % cfg.MIN_FUSED_SCORE)
if not gap_ok:
    print("     域内: %s" % [round(x, 5) for x in in_scores])
    print("     域外: %s" % [round(x, 5) for x in out_scores])

from app.rag import build_evidence  # noqa: E402
e_in = build_evidence("成都必去的景点")
e_out = build_evidence("量子纠缠退相干实验的低温恒温器型号")
e_f = build_evidence("有什么好玩的景点", city="成都")
check("域内查询 no_evidence=False", e_in["no_evidence"] is False,
      "max=%.5f 条数=%d" % (e_in["max_fused_score"], len(e_in["chunks"])))
check("域外查询 no_evidence=True", e_out["no_evidence"] is True,
      "max=%.5f 条数=%d" % (e_out["max_fused_score"], len(e_out["chunks"])))
check("带过滤不被阈值误杀", len(e_f["chunks"]) > 0,
      "%d 条 max=%.5f no_evidence=%s" % (len(e_f["chunks"]), e_f["max_fused_score"], e_f["no_evidence"]))

if a.twice:
    from app.rag.indexing.build import build_index  # noqa: E402
    m1 = build_index(rebuild=True, verbose=False)
    m2 = build_index(rebuild=False, verbose=False)
    check("两次构建 chunk 数一致", m1["chunk_count"] == m2["chunk_count"],
          "%s vs %s" % (m1["chunk_count"], m2["chunk_count"]))
    check("两次构建维度一致", m1["embedding_dim"] == m2["embedding_dim"],
          "%s vs %s" % (m1["embedding_dim"], m2["embedding_dim"]))

print("-" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：", "、".join(FAIL))
sys.exit(1 if FAIL else 0)
