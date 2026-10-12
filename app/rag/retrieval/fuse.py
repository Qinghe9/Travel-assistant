# -*- coding: utf-8 -*-
"""多路召回融合（向量 + BM25 + 关键词/标题）、重排、去重、阈值与空结果。

三路召回
  vector  语义相近      （hot_index 内存暴力余弦）
  bm25    词法相关      （倒排候选 + 向量化打分）
  keyword 关键词/标题精确命中（覆盖率 + 标题加权），专治专名、景点名、地名
融合用 RRF（按名次而非分数，避免不同路的分数量纲不可比），同分按「最早出现的路」优先。

阈值设计：
  - 无过滤的开放式查询：应用余弦相似度阈值与 RRF 分阈值（实测域外最高 0.01639 = 单路孤证，
    域内最低 0.02862 = 两路共识；余弦域内 0.53~0.76 / 域外 0.36~0.48）。
  - 带过滤（city/category/province）的查询：过滤条件本身就是用户意图的强表达，
    候选池变小会导致两路共识减少、分数整体下移，此时不应用 RRF 阈值，否则会误杀合理问题；
    但仍把 max_fused_score 与余弦相似度上报给调用方。
"""

from __future__ import annotations

from app.rag import config as cfg


def rrf_fuse(vec_ids, bm25_ids=None, keyword_ids=None, k: int = None, top_n: int = None,
             weights=None):
    """多路 RRF：score += w_i/(k+rank)；同分按「最早出现的路」优先（向量 > BM25 > 关键词）。

    兼容旧调用：rrf_fuse(vec_ids, bm25_ids) 行为与原实现一致（同分优先向量路）。
    """
    k = k or cfg.RRF_K
    top_n = top_n or cfg.TOP_K_FINAL
    lists = [lst or [] for lst in (vec_ids, bm25_ids, keyword_ids)]
    w = list(weights) if weights else []
    while len(w) < len(lists):
        w.append(1.0)
    scores, prio = {}, {}
    for li, lst in enumerate(lists):
        for rank, cid in enumerate(lst, start=1):
            scores[cid] = scores.get(cid, 0.0) + w[li] / (k + rank)
            if cid not in prio:
                prio[cid] = li
    order = sorted(scores.items(), key=lambda kv: (-kv[1], prio.get(kv[0], 99), kv[0]))
    return order[:top_n], scores


def dedup_by_parent(items, max_per_parent: int = None):
    """同一 parent_doc_id 最多保留 max_per_parent 条。"""
    max_per_parent = max_per_parent or cfg.MAX_PER_PARENT
    seen, out = {}, []
    for it in items:
        pid = (it.get("metadata") or {}).get("parent_doc_id") or it.get("chunk_id")
        if seen.get(pid, 0) >= max_per_parent:
            continue
        seen[pid] = seen.get(pid, 0) + 1
        out.append(it)
    return out


def search_with_score(query: str, city: str = None, category: str = None, province: str = None,
                      top_k: int = None, source_type: str = None, min_score: float = None,
                      rerank: bool = None):
    """多路混合检索主入口。返回 (items, max_fused_score)。rerank=None 时用配置开关。"""
    import time as _t
    from app.rag.retrieval import recall as R
    top_k = top_k or cfg.TOP_K_FINAL
    filters = {"city": city, "category": category, "province": province, "source_type": source_type}
    has_filter = any(filters.values())
    use_rerank = cfg.RERANK_ENABLED if rerank is None else rerank

    v = R.vector_search(query, city=city, category=category, province=province, source_type=source_type)
    b = R.bm25_search(query, city=city, category=category, province=province, source_type=source_type)
    kw = (R.keyword_search(query, city=city, category=category, province=province,
                           source_type=source_type) if cfg.KEYWORD_ENABLED else [])
    if kw and cfg.KEYWORD_MIN_COVERAGE > 0:
        kw = [x for x in kw if (x.get("coverage") or 0.0) >= cfg.KEYWORD_MIN_COVERAGE]

    need_ids = [x["chunk_id"] for x in b] + [x["chunk_id"] for x in kw]
    meta = R.load_meta(need_ids) if need_ids else {}
    if has_filter:
        want = {k: val for k, val in filters.items() if val}

        def _keep(x):
            md = (meta.get(x["chunk_id"], {}).get("metadata") or {})
            return all(md.get(k) == val for k, val in want.items())

        b = [x for x in b if _keep(x)]
        kw = [x for x in kw if _keep(x)]

    _t0 = _t.perf_counter()
    # 开了重排就多留候选（默认 20 条），让重排有得选；否则维持原 3 倍
    cand_n = max(top_k * 3, cfg.RERANK_CANDIDATES) if use_rerank else top_k * 3
    # 三路一起进 RRF：关键词路按**名次**参与，才能把标题/专名精确命中的候选推进重排池
    # （早期版本把关键词候选追加在池尾，被重排的候选截断吃掉，等于白召回）。
    fused, scores = rrf_fuse([x["chunk_id"] for x in v], [x["chunk_id"] for x in b],
                             [x["chunk_id"] for x in kw], top_n=cand_n,
                             weights=[1.0, 1.0, cfg.KEYWORD_WEIGHT])
    top_score = max(scores.values()) if scores else 0.0
    top_sim = max([x["score_vector"] for x in v], default=0.0)
    R.LAST_SIM = top_sim
    R.TIMING["fuse_ms"] += (_t.perf_counter() - _t0) * 1000
    thr = cfg.MIN_FUSED_SCORE if min_score is None else min_score

    # 判据一（始终生效）：向量余弦相似度。绝对值有语义，域内 0.53+ / 域外 <0.49
    if fused and cfg.MIN_VECTOR_SIM and top_sim < cfg.MIN_VECTOR_SIM:
        return [], top_score
    # 判据二（仅无过滤时）：RRF 融合分，挡掉「单路孤证」
    if not has_filter and fused and thr and top_score < thr:
        return [], top_score
    if not fused:
        return [], 0.0

    vmap = {x["chunk_id"]: x for x in v}
    need = [cid for cid, _ in fused if cid not in vmap and cid not in meta]
    if need:
        meta.update(R.load_meta(need))

    bmap = {x["chunk_id"]: x for x in b}
    kmap = {x["chunk_id"]: x for x in kw}
    items = []
    for cid, sc in fused:
        src = vmap.get(cid) or meta.get(cid, {})
        md = src.get("metadata") or {}
        items.append({
            "chunk_id": cid,
            "text": src.get("text") or "",
            "score_vector": vmap.get(cid, {}).get("score_vector"),
            "score_bm25": bmap.get(cid, {}).get("score_bm25"),
            "score_keyword": kmap.get(cid, {}).get("score_keyword"),
            "score_fused": sc,
            "metadata": md,
        })
    items = [it for it in items if it["text"]]

    # 重排：只用重排分决定"顺序"，阈值判定仍用重排前的余弦/RRF 分（语义不变）
    if use_rerank and len(items) > 1:
        from app.rag.retrieval import rerank as _rr
        _t1 = _t.perf_counter()
        items = _rr.rerank(query, items)
        R.TIMING["rerank_ms"] += (_t.perf_counter() - _t1) * 1000

    items = dedup_by_parent(items)
    return items[:top_k], top_score


def top_similarity(query: str, **filters):
    """只算向量最高余弦相似度（备用；主路径由 search_with_score 顺带给出）。"""
    from app.rag.retrieval import recall as R
    v = R.vector_search(query, top_k=1, **filters)
    return max([x["score_vector"] for x in v], default=0.0)


def search(query: str, city: str = None, category: str = None, province: str = None,
           top_k: int = None, source_type: str = None):
    """对外接口：返回 TopK 证据列表。"""
    return search_with_score(query, city=city, category=category, province=province,
                             top_k=top_k, source_type=source_type)[0]
