# -*- coding: utf-8 -*-
"""证据装配与 source_id 引用绑定。"""

from __future__ import annotations

import hashlib
import json
import time

from app.rag import config as cfg


def source_id(source_url: str, chunk_id: str) -> str:
    """sha1(source_url + '#' + chunk_id)[:12]。"""
    return hashlib.sha1(("%s#%s" % (source_url, chunk_id)).encode("utf-8")).hexdigest()[:12]


def build_evidence(query: str, city: str = None, category: str = None, top_k: int = None) -> dict:
    from app.rag.retrieval import recall as _R
    from app.rag.retrieval.fuse import search_with_score
    _R.reset_timing()
    t0 = time.perf_counter()
    hits, max_score = search_with_score(query, city=city, category=category, top_k=top_k)
    latency = int((time.perf_counter() - t0) * 1000)

    citations, chunks = [], []
    seen = set()
    for h in hits:
        md = h["metadata"] or {}
        url = md.get("source_url", "")
        sid = source_id(url, h["chunk_id"])
        chunks.append({"chunk_id": h["chunk_id"], "source_id": sid, "text": h["text"],
                       "score_fused": h["score_fused"], "metadata": md})
        if sid not in seen:
            seen.add(sid)
            citations.append({"source_id": sid,
                              "title": md.get("doc_title") or md.get("title") or "",
                              "title_path": md.get("title_path", ""),
                              "url": url, "retrieved_at": md.get("retrieved_at", ""),
                              "updated_at": md.get("updated_at", ""),
                              "province": md.get("province", ""), "city": md.get("city", ""),
                              "category": md.get("category", "")})
    sim = _R.LAST_SIM
    # 与 fuse 的判据保持一致：**带过滤的查询不套 RRF 阈值**。
    # 过滤后候选池变小、单路证据变多、融合分整体下移（单路第一名恒为 1/(k+1)≈0.0164 < 0.018），
    # 在这里套阈值会把明明检索到的合理问题误判成"无证据"而拒答——实测多轮追问就是这种情况。
    has_filter = bool(city or category)
    weak = bool(chunks) and (
        (not has_filter and cfg.MIN_FUSED_SCORE and max_score < cfg.MIN_FUSED_SCORE)
        or (not has_filter and cfg.MIN_VECTOR_SIM and sim < cfg.MIN_VECTOR_SIM))
    index_version = ""
    if cfg.MANIFEST_FILE.exists():
        index_version = json.loads(cfg.MANIFEST_FILE.read_text(encoding="utf-8")).get("index_version", "")
    return {
        "query": query,
        "city": city,
        "category": category,
        "chunks": chunks,
        "citations": citations,
        "tool_refs": [],
        "retrieval_latency_ms": latency,
        "degraded": not bool(chunks),
        "index_version": index_version,
        "empty": not chunks,
        "max_fused_score": round(max_score, 6),
        "max_vector_sim": round(sim, 4),
        "timings": {k: round(v, 1) for k, v in _R.TIMING.items()},
        "no_evidence": (not chunks) or weak,
    }
