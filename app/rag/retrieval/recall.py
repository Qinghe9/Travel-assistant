# -*- coding: utf-8 -*-
"""召回：向量召回 + BM25 召回（元数据过滤下推到召回层）。"""

from __future__ import annotations

import pickle

from app.rag import config as cfg
from app.rag.indexing.embed import Embedder

_EMB = None
_BM25 = None
# 四段耗时（embed_ms / vector_ms / bm25_ms / fuse_ms）+ 元数据读取
TIMING = {"embed_ms": 0.0, "vector_ms": 0.0, "bm25_ms": 0.0, "fuse_ms": 0.0, "meta_ms": 0.0}
# 最近一次检索的向量最高相似度（避免调用方为拿这个值再检索一遍）
LAST_SIM = 0.0


def reset_timing():
    for k in TIMING:
        TIMING[k] = 0.0


def embedder() -> Embedder:
    global _EMB
    if _EMB is None:
        _EMB = Embedder(verbose=False)
    return _EMB


def bm25_pack():
    global _BM25
    if _BM25 is None:
        if not cfg.BM25_FILE.exists():
            raise RuntimeError("缺少 %s，请先运行 scripts/build_index.py" % cfg.BM25_FILE)
        with cfg.BM25_FILE.open("rb") as fh:
            _BM25 = pickle.load(fh)
    return _BM25


def warmup():
    """服务启动时调用：预热 jieba、BM25、向量器，并真跑一次检索把 Chroma 客户端初始化掉。
    不做这一步，首个真实请求要额外付 3-12 秒冷启动（实测）。"""
    import jieba
    jieba.initialize()
    bm25_pack()
    embedder()
    try:
        from app.rag.retrieval.fuse import search_with_score
        search_with_score("预热查询", top_k=1)
    except Exception:  # noqa: BLE001 - 预热失败不应影响启动
        pass


def _where(filters: dict | None):
    if not filters:
        return None
    conds = [{k: v} for k, v in filters.items() if v]
    if not conds:
        return None
    return conds[0] if len(conds) == 1 else {"$and": conds}


def vector_search(query: str, top_k: int = None, city: str = None, category: str = None,
                  province: str = None, source_type: str = None):
    """返回 [{chunk_id, text, score_vector, metadata}]，按相似度降序。"""
    import time
    from app.rag.indexing.build import get_client
    top_k = top_k or cfg.TOP_K_VECTOR
    t0 = time.perf_counter()
    vec = embedder().embed_texts([query])[0]
    TIMING["embed_ms"] += (time.perf_counter() - t0) * 1000
    col = get_client().get_collection(cfg.COLLECTION)
    t1 = time.perf_counter()
    res = col.query(query_embeddings=[vec], n_results=top_k,
                    where=_where({"city": city, "category": category,
                                  "province": province, "source_type": source_type}),
                    include=["documents", "metadatas", "distances"])
    TIMING["vector_ms"] += (time.perf_counter() - t1) * 1000
    out = []
    for i, cid in enumerate(res["ids"][0]):
        dist = res["distances"][0][i]
        out.append({"chunk_id": cid, "text": res["documents"][0][i],
                    "score_vector": 1.0 - float(dist), "metadata": res["metadatas"][0][i]})
    return out


def bm25_search(query: str, top_k: int = None, city: str = None, category: str = None,
                province: str = None, source_type: str = None):
    """词法召回；过滤条件在打分后按元数据筛（BM25 无内建过滤）。"""
    import time
    import jieba
    pack = bm25_pack()
    top_k = top_k or cfg.TOP_K_BM25
    t0 = time.perf_counter()
    scores = pack["bm25"].get_scores(list(jieba.cut(query)))
    TIMING["bm25_ms"] += (time.perf_counter() - t0) * 1000
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[: top_k * 6]
    return [{"chunk_id": pack["ids"][i], "score_bm25": float(scores[i])} for i in order]


def load_meta(chunk_ids):
    """从 Chroma 取元数据与正文（首次调用会初始化 Chroma 客户端，计入 meta_ms）。"""
    import time
    from app.rag.indexing.build import get_client
    t0 = time.perf_counter()
    col = get_client().get_collection(cfg.COLLECTION)
    res = col.get(ids=list(chunk_ids), include=["documents", "metadatas"])
    TIMING["meta_ms"] += (time.perf_counter() - t0) * 1000
    return {cid: {"text": doc, "metadata": meta}
            for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])}
