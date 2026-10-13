# -*- coding: utf-8 -*-
"""召回：向量召回 + BM25 召回（元数据过滤下推到召回层）。

热索引（hot_index.py）可用时走内存路径：向量暴力余弦 + 倒排候选打分 + 元数据查表，
把单次检索从 ~480ms 压到 ~40ms；构建失败自动回退到下面的 Chroma 路径。
"""

from __future__ import annotations

import pickle

from app.rag import config as cfg
from app.rag.indexing.embed import Embedder
from app.rag.retrieval import hot_index

_EMB = None
_BM25 = None
# 四段耗时（embed_ms / vector_ms / bm25_ms / fuse_ms）+ 元数据读取
TIMING = {"embed_ms": 0.0, "vector_ms": 0.0, "bm25_ms": 0.0, "keyword_ms": 0.0,
          "fuse_ms": 0.0, "rerank_ms": 0.0, "meta_ms": 0.0}
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
    """服务启动时调用：预热 jieba、BM25、向量器、热索引，并真跑一次检索把链路打通。
    不做这一步，首个真实请求要额外付 3-12 秒冷启动（实测）。"""
    import jieba
    import jieba.analyse
    jieba.initialize()
    jieba.analyse.extract_tags("预热")   # TF-IDF 词典首次加载 ~0.7s，别让它算在第一个问题上
    pack = bm25_pack()
    embedder()
    hot_index.build(pack, verbose=True)   # 装进内存；失败自动回退 Chroma
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
    top_k = top_k or cfg.TOP_K_VECTOR
    t0 = time.perf_counter()
    vec = embedder().embed_query(query)
    TIMING["embed_ms"] += (time.perf_counter() - t0) * 1000
    if hot_index.ready():          # 内存暴力余弦：实测 126ms -> ~5ms
        t1 = time.perf_counter()
        out = hot_index.vector_search(vec, top_k, {"city": city, "category": category,
                                                   "province": province, "source_type": source_type})
        TIMING["vector_ms"] += (time.perf_counter() - t1) * 1000
        return out
    from app.rag.indexing.build import get_client
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
    terms = list(jieba.cut(query))
    if hot_index.ready():          # 倒排候选打分：实测 130ms -> ~10ms
        out = hot_index.bm25_search(terms, top_k)
        TIMING["bm25_ms"] += (time.perf_counter() - t0) * 1000
        return out
    scores = pack["bm25"].get_scores(terms)
    TIMING["bm25_ms"] += (time.perf_counter() - t0) * 1000
    order = sorted(range(len(scores)), key=lambda i: -scores[i])[: top_k * 6]
    return [{"chunk_id": pack["ids"][i], "score_bm25": float(scores[i])} for i in order]


def keywords_of(query: str, top_k: int = 8) -> list:
    """抽取检索用关键词：TF-IDF 关键词 + 地名/专名（避免罕见景点名被 TF-IDF 排掉）。"""
    import jieba.analyse
    import jieba.posseg as pseg
    tags = list(jieba.analyse.extract_tags(query, topK=top_k))
    extra = []
    for word, flag in pseg.cut(query):
        if flag in ("ns", "nz", "nr", "nt", "n") and len(word) > 1 and word not in tags:
            extra.append(word)
    return (tags + extra)[: top_k + 4]


def exact_phrases_of(query: str) -> list:
    """抽取用户明确给出的长专名，供正文子串精确召回。"""
    import re
    q = (query or "").strip()
    patterns = [
        # 优先取“城市的实体在哪里”中间部分，不能让 ^ 分支贪婪吞掉城市。
        r"的\s*([\u4e00-\u9fffA-Za-z0-9·]{2,30}?)\s*(?:在哪里|在哪|怎么去|地址)",
        r"[“\"《]([^”\"》]{2,30})[”\"》]",
    ]
    generic = {"景点", "美食", "酒店", "住宿", "交通", "开放时间", "门票", "价格"}
    out = []
    for pat in patterns:
        for value in re.findall(pat, q):
            value = value.strip()
            if value not in generic and value not in out:
                out.append(value)
    return out[:3]


def keyword_search(query: str, top_k: int = None, city: str = None, category: str = None,
                   province: str = None, source_type: str = None):
    """第 3 路：关键词/标题精确匹配召回。热索引不可用时返回空（不阻断）。"""
    import time
    top_k = top_k or cfg.TOP_K_KEYWORD
    t0 = time.perf_counter()
    if not hot_index.ready():
        TIMING["keyword_ms"] += (time.perf_counter() - t0) * 1000
        return []
    terms = keywords_of(query)
    out = hot_index.keyword_search(terms, top_k, {"city": city, "category": category,
                                                  "province": province, "source_type": source_type},
                                   phrases=exact_phrases_of(query))
    TIMING["keyword_ms"] += (time.perf_counter() - t0) * 1000
    return out


def load_meta(chunk_ids):
    """取元数据与正文：热索引命中则查内存，缺失的才回 Chroma（计入 meta_ms）。"""
    import time
    ids = list(chunk_ids)
    t0 = time.perf_counter()
    out, missing = {}, ids
    if hot_index.ready():
        out = hot_index.lookup(ids)
        missing = [cid for cid in ids if cid not in out]
    if missing:
        from app.rag.indexing.build import get_client
        col = get_client().get_collection(cfg.COLLECTION)
        res = col.get(ids=list(missing), include=["documents", "metadatas"])
        out.update({cid: {"text": doc, "metadata": meta}
                    for cid, doc, meta in zip(res["ids"], res["documents"], res["metadatas"])})
    TIMING["meta_ms"] += (time.perf_counter() - t0) * 1000
    return out
