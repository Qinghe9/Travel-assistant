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


def _meta_match(meta: dict, item: dict, want: dict) -> bool:
    """候选的元数据是否满足过滤条件（want 已去掉空值）。"""
    md = (meta.get(item["chunk_id"], {}).get("metadata") or {})
    return all(md.get(k) == val for k, val in want.items())


def search_with_score(query: str, city: str = None, category: str = None, province: str = None,
                      top_k: int = None, source_type: str = None, min_score: float = None,
                      rerank: bool = None, entity: str = None):
    """多路混合检索主入口。返回 (items, max_fused_score)。rerank=None 时用配置开关。"""
    import time as _t
    from app.rag.retrieval import recall as R
    top_k = top_k or cfg.TOP_K_FINAL
    filters = {"city": city, "category": category, "province": province, "source_type": source_type}
    has_filter = any(filters.values())
    use_rerank = cfg.RERANK_ENABLED if rerank is None else rerank
    query_phrases = R.exact_phrases_of(query, entity=entity)

    def _recall(cat):
        bb = R.bm25_search(query, city=city, category=cat, province=province, source_type=source_type)
        kk = (R.keyword_search(query, city=city, category=cat, province=province,
                               source_type=source_type, entity=entity) if cfg.KEYWORD_ENABLED else [])
        ids = [x["chunk_id"] for x in bb] + [x["chunk_id"] for x in kk]
        mm = R.load_meta(ids) if ids else {}
        if has_filter:                      # BM25/关键词路无内建过滤，按元数据后置筛
            w = {k: val for k, val in filters.items() if val}
            bb = [x for x in bb if _meta_match(mm, x, w)]
            kk = [x for x in kk if _meta_match(mm, x, w)]
        lexical_exact = bool(query_phrases and any(x.get("exact_phrase") for x in kk))
        # 明确专名已被正文/标题完整命中时，词法证据比语义近似更可靠，并可省掉一次远程 embedding。
        vv = ([] if lexical_exact else
              R.vector_search(query, city=city, category=cat, province=province, source_type=source_type))
        return vv, bb, kk, mm, lexical_exact

    v, b, kw, meta, lexical_exact = _recall(category)
    # 类别标注本身不可靠（实测 tips 占 53.9%，成都 66 个 chunk 里 food=0，餐饮内容被标成 tips），
    # 硬按 category 过滤会把正确内容整池滤空。滤空后自动退回"只按城市"重试一次（约 20ms）。
    # 注意判断必须放在元数据过滤**之后**：bm25/keyword 的原始返回不受 category 影响，
    # 早先放在前面判过一次，导致回退永远不触发。
    if category and not v and not b and not kw:
        category = None
        filters["category"] = None
        has_filter = any(filters.values())
        v, b, kw, meta, lexical_exact = _recall(None)
    if kw and cfg.KEYWORD_MIN_COVERAGE > 0:
        kw = [x for x in kw if (x.get("coverage") or 0.0) >= cfg.KEYWORD_MIN_COVERAGE]

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

    # 无过滤时用余弦阈值挡域外问题。已有城市/类别过滤时，过滤本身就是强意图；
    # 继续套全库阈值会误杀专名、地址、开放时间等短查询。
    if not lexical_exact and not has_filter and fused and cfg.MIN_VECTOR_SIM and top_sim < cfg.MIN_VECTOR_SIM:
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
            "exact_phrase": bool(kmap.get(cid, {}).get("exact_phrase")),
            "score_fused": sc,
            "metadata": md,
        })
    items = [it for it in items if it["text"]]

    # 完整实体短语命中是比通用词多路共识更强的信号，确定性推到 Top3。
    if cfg.EXACT_PHRASE_BOOST:
        for it in items:
            if it.get("exact_phrase"):
                it["score_fused"] = (it.get("score_fused") or 0.0) + cfg.EXACT_PHRASE_BOOST
        items.sort(key=lambda x: -(x.get("score_fused") or 0.0))

    # 城市条目优先：查询限定了城市时，来自该城市自身条目（doc_title == city）的 chunk 加权。
    # 实测反例："广州有什么旅游景点" 的 Top3 全来自省级文档《广东》的行政区划/交通段落，
    # 而《广州》条目里的景点内容被挤在后面。
    if city and cfg.CITY_DOC_BOOST:
        for it in items:
            if (it.get("metadata") or {}).get("doc_title") == city:
                it["score_fused"] = (it.get("score_fused") or 0.0) + cfg.CITY_DOC_BOOST
        items.sort(key=lambda x: -(x.get("score_fused") or 0.0))

    # 重排：只用重排分决定"顺序"，阈值判定仍用重排前的余弦/RRF 分（语义不变）
    if use_rerank and len(items) > 1:
        from app.rag.retrieval import rerank as _rr
        _t1 = _t.perf_counter()
        items = _rr.rerank(query, items, enabled=use_rerank)
        R.TIMING["rerank_ms"] += (_t.perf_counter() - _t1) * 1000

    items = dedup_by_parent(items)
    return items[:top_k], top_score


def top_similarity(query: str, **filters):
    """只算向量最高余弦相似度（备用；主路径由 search_with_score 顺带给出）。"""
    from app.rag.retrieval import recall as R
    v = R.vector_search(query, top_k=1, **filters)
    return max([x["score_vector"] for x in v], default=0.0)


def search(query: str, city: str = None, category: str = None, province: str = None,
           top_k: int = None, source_type: str = None, entity: str = None):
    """对外接口：返回 TopK 证据列表。"""
    return search_with_score(query, city=city, category=category, province=province,
                             top_k=top_k, source_type=source_type, entity=entity)[0]
