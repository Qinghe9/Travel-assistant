# -*- coding: utf-8 -*-
"""检索适配层：把 RAG 模块包装成「可降级」的调用。

为什么要包一层：RAG 层是可选依赖（需要 chromadb / jieba / rank_bm25、向量模型密钥，
还需要本地索引）。这些缺一个检索就起不来。Agent 的正确行为是「如实说没有证据」，
而不是整个对话崩掉 —— 对应 PRD 第 7 章的降级策略。
"""

from __future__ import annotations

from app.agent import config as cfg


def _degraded(query: str, reason: str) -> dict:
    return {
        "query": query, "chunks": [], "citations": [],
        "no_evidence": True, "degraded": True, "empty": True,
        "retrieval_latency_ms": 0, "max_fused_score": 0.0, "max_vector_sim": 0.0,
        "timings": {}, "index_version": "", "error": reason,
    }


def build_evidence(query: str, city: str = None, category: str = None,
                   entity: str = None, top_k: int = None) -> dict:
    """调用 RAG 混合检索；任何异常都降级为「无证据」并带上原因。"""
    try:
        from app.rag.retrieval.evidence import build_evidence as _impl
    except Exception as exc:
        return _degraded(query, "RAG 依赖不可用：%s: %s" % (type(exc).__name__, exc))
    try:
        return _impl(query, city=city, category=category, entity=entity,
                     top_k=top_k or cfg.TOP_K_FINAL)
    except Exception as exc:
        return _degraded(query, "检索失败：%s: %s" % (type(exc).__name__, exc))


def to_chunks(evidence: dict) -> list:
    """把 RAG 返回的 chunks 归一成证据条目（保留 metadata，供组装证据块使用）。"""
    out = []
    for chunk in evidence.get("chunks") or []:
        md = chunk.get("metadata") or {}
        out.append({
            "source_id": chunk.get("source_id", ""),
            "title": md.get("doc_title") or md.get("title") or "",
            "url": md.get("source_url", ""),
            "text": chunk.get("text", ""),
            "score": chunk.get("score_fused") or 0.0,
            "updated_at": md.get("updated_at", ""),
            "source_type": md.get("source_type", "public"),
            "city": md.get("city", ""),
            "kind": "knowledge",
            "metadata": md,
        })
    return out


def meta_of(evidence: dict, latency_ms: int = 0) -> dict:
    """检索元信息：用于轨迹与验收（相似度、耗时、是否降级、索引版本）。"""
    return {
        "no_evidence": bool(evidence.get("no_evidence")),
        "degraded": bool(evidence.get("degraded")),
        "max_vector_sim": evidence.get("max_vector_sim", 0.0),
        "max_fused_score": evidence.get("max_fused_score", 0.0),
        "retrieval_latency_ms": evidence.get("retrieval_latency_ms", latency_ms),
        "index_version": evidence.get("index_version", ""),
        "timings": evidence.get("timings", {}),
    }
