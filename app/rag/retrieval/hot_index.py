# -*- coding: utf-8 -*-
"""进程内热索引：把向量矩阵、正文元数据、词法倒排一次性装进内存，消掉检索热点。

动机（12k chunk 实测，单次检索）：
    Chroma 带 where 查询   115 ms  →  内存矩阵乘法 + 布尔掩码   ~5 ms
    BM25 全库打分          130 ms  →  倒排候选打分              ~10 ms
    按 id 回表(60 条)      101 ms  →  内存字典查表              ~0.1 ms

一致性保证：
  * 向量取自 Chroma 的同一批 embedding（已 L2 归一化），点积即余弦，
    与原来的 score_vector = 1 - cosine_distance 数值等价；
  * BM25 复用同一份 bm25.pkl 的 idf / doc_len / avgdl / k1 / b，只对「含查询词」的文档求值，
    这些文档的分数与 rank_bm25 全库打分逐位相同（不含查询词的文档分数恒为 0，不影响 TopK）。
  * 元数据按 cfg.METADATA_FIELDS 归一化（None -> ""），与写入 Chroma 时的 _clean_meta 一致。

任何一步失败都只是返回 None，调用方回退到原来的 Chroma 路径。
"""

from __future__ import annotations

import json
from collections import Counter

from app.rag import config as cfg

HOT_FIELDS = ("city", "category", "province", "source_type")
VEC_CACHE = cfg.INDEX / "hot_vectors.npz"

_HOT = None


def ready() -> bool:
    return _HOT is not None


def get():
    return _HOT


def _fingerprint(ids) -> str:
    version = ""
    try:
        version = json.loads(cfg.MANIFEST_FILE.read_text(encoding="utf-8")).get("index_version", "")
    except Exception:  # noqa: BLE001
        pass
    return "%s|%d|%s|%s" % (version, len(ids), ids[0] if ids else "", ids[-1] if ids else "")


def _load_matrix(np, ids):
    """取 (n, dim) 归一化向量矩阵：优先读 npz 缓存，否则从 Chroma 全量拉一次并写缓存。"""
    fp = _fingerprint(ids)
    if VEC_CACHE.exists():
        try:
            z = np.load(str(VEC_CACHE), allow_pickle=False)
            if z["mat"].shape[0] == len(ids) and str(z["fingerprint"]) == fp:
                return z["mat"]
        except Exception:  # noqa: BLE001 - 缓存损坏就当没有
            pass
    from app.rag.indexing.build import get_client

    col = get_client().get_collection(cfg.COLLECTION)
    got = col.get(include=["embeddings"])
    by_id = {cid: v for cid, v in zip(got["ids"], got["embeddings"])}
    missing = [cid for cid in ids if cid not in by_id]
    if missing:
        raise RuntimeError("Chroma 缺 %d 个 chunk（索引与 chunks.jsonl 不同步）" % len(missing))
    mat = np.asarray([by_id[cid] for cid in ids], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    mat = mat / np.where(norms == 0, 1.0, norms)
    try:
        np.savez(str(VEC_CACHE), mat=mat, fingerprint=np.array(fp))
    except Exception:  # noqa: BLE001 - 缓存写不进去不影响功能
        pass
    return mat


def build(pack, verbose: bool = False):
    """构建热索引；成功返回 _HOT，失败返回 None（调用方继续用 Chroma）。"""
    global _HOT
    if _HOT is not None:
        return _HOT
    try:
        import numpy as np
        import time as _t
    except ImportError:
        return None
    try:
        t0 = _t.perf_counter()
        ids = list(pack["ids"])
        bm = pack["bm25"]

        raw = {}
        with cfg.CHUNKS_FILE.open(encoding="utf-8") as fh:
            for line in fh:
                c = json.loads(line)
                raw[c["chunk_id"]] = c
        t1 = _t.perf_counter()

        fields = tuple(getattr(cfg, "METADATA_FIELDS", HOT_FIELDS))
        text, meta = [], []
        for cid in ids:
            c = raw.get(cid) or {}
            text.append(c.get("text", ""))
            meta.append({k: ("" if c.get(k) is None else c.get(k)) for k in fields})
        raw = None
        t2 = _t.perf_counter()

        # 倒排：term -> ndarray (m, 2)，每行 [行号, 词频]，便于 numpy 向量化打分
        buckets = {}
        for row, toks in enumerate(pack["tokens"]):
            for term, freq in Counter(toks).items():
                buckets.setdefault(term, []).extend((row, freq))
        post = {t: np.asarray(v, dtype=np.int32).reshape(-1, 2) for t, v in buckets.items()}
        buckets = None

        # 标题倒排：doc_title + title_path 的分词（只存行号），用于第 3 路关键词精确匹配
        import jieba
        title_buckets = {}
        for row, md in enumerate(meta):
            title = "%s %s" % (md.get("doc_title") or "", md.get("title_path") or "")
            if not title.strip():
                continue
            for term in set(jieba.cut(title)):
                title_buckets.setdefault(term, []).append(row)
        title_post = {t: np.asarray(v, dtype=np.int32) for t, v in title_buckets.items()}
        title_buckets = None
        t3 = _t.perf_counter()

        k1, b = float(bm.k1), float(bm.b)
        dl = np.asarray(bm.doc_len, dtype=np.float64)
        kvec = k1 * (1.0 - b + b * dl / float(bm.avgdl))

        mat = _load_matrix(np, ids)
        t4 = _t.perf_counter()
        _HOT = {
            "n": len(ids),
            "ids": ids,
            "row": {cid: i for i, cid in enumerate(ids)},
            "text": text,
            "meta": meta,
            "post": post,
            "title_post": title_post,
            "idf": bm.idf,
            "k1": k1,
            "k1p1": k1 + 1.0,
            "kvec": kvec,
            "mat": mat,
            "masks": {},
            "np": np,
            "_range": np.arange(len(ids)),
        }
        if verbose:
            print("  热索引就绪：%d chunk × %d 维，倒排词 %d 个" % (len(ids), mat.shape[1], len(post)))
            print("  构建用时：读 chunk %.2fs | 元数据 %.2fs | 倒排 %.2fs | 向量 %.2fs | 合计 %.2fs"
                  % (t1 - t0, t2 - t1, t3 - t2, t4 - t3, _t.perf_counter() - t0))
        return _HOT
    except Exception as exc:  # noqa: BLE001 - 热索引失败必须能回退
        if verbose:
            print("  热索引构建失败（回退 Chroma）：%s" % exc)
        _HOT = None
        return None


def _mask(field: str, value: str):
    h = _HOT
    cache = h["masks"].setdefault(field, {})
    m = cache.get(value)
    if m is None:
        np = h["np"]
        m = np.fromiter((1 if md.get(field) == value else 0 for md in h["meta"]),
                        dtype=bool, count=h["n"])
        cache[field] = m
    return m


def candidate_rows(filters):
    """返回命中的行号数组（np.ndarray）；无过滤返回 None 表示全量。"""
    h = _HOT
    mask = None
    for field in HOT_FIELDS:
        value = (filters or {}).get(field)
        if not value:
            continue
        m = _mask(field, value)
        mask = m if mask is None else (mask & m)
    if mask is None:
        return None
    return h["np"].flatnonzero(mask)


def vector_search(qvec, top_k: int, filters=None):
    """内存暴力余弦检索：返回 [{chunk_id, text, score_vector, metadata}]，按相似度降序。"""
    h = _HOT
    np = h["np"]
    q = np.asarray(qvec, dtype=np.float32)
    norm = float(np.linalg.norm(q))
    if norm:
        q = q / norm
    rows = candidate_rows(filters)
    if rows is None:
        sub = h["mat"]
        rows = h["_range"]
    else:
        if len(rows) == 0:
            return []
        sub = h["mat"][rows]
    sims = sub @ q
    k = min(int(top_k), len(sims))
    if k <= 0:
        return []
    if k < len(sims):
        picked = np.argpartition(-sims, k - 1)[:k]
    else:
        picked = np.arange(len(sims))
    picked = picked[np.argsort(-sims[picked], kind="stable")]
    out = []
    for p in picked:
        r = int(rows[p])
        out.append({"chunk_id": h["ids"][r], "text": h["text"][r],
                    "score_vector": float(sims[p]), "metadata": h["meta"][r]})
    return out


def bm25_search(terms, top_k: int):
    """倒排候选打分（numpy 向量化）：数值与 rank_bm25 全库打分一致，但只遍历含查询词的文档。"""
    h = _HOT
    np = h["np"]
    post, idf = h["post"], h["idf"]
    k1p1, kvec = h["k1p1"], h["kvec"]
    scores = np.zeros(h["n"], dtype=np.float64)
    touched = False
    for t in terms:
        w = idf.get(t)
        if not w:
            continue
        e = post.get(t)
        if e is None:
            continue
        rows, freq = e[:, 0], e[:, 1]
        scores[rows] += w * freq * k1p1 / (freq + kvec[rows])
        touched = True
    if not touched:
        return []
    nz = np.flatnonzero(scores)
    k = min(int(top_k) * 6, len(nz))
    if k <= 0:
        return []
    picked = nz[np.argsort(-scores[nz], kind="stable")[:k]]
    return [{"chunk_id": h["ids"][int(r)], "score_bm25": float(scores[r])} for r in picked]


def keyword_search(terms, top_k: int, filters=None, title_boost: float = None):
    """第 3 路：关键词/标题精确匹配。

    覆盖率为底、标题命中加权：score = (命中词数 + boost × 标题命中词数) / 查询词数。
    与向量路一样在**过滤后的子集内**排序（关键词路是新加的，不受历史基线约束），
    因此带 city/category 时不会把外城条目排进来。
    """
    h = _HOT
    np = h["np"]
    boost = 2.0 if title_boost is None else title_boost
    uniq = []
    seen = set()
    for t in terms:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            uniq.append(t)
    if not uniq:
        return []
    hit = np.zeros(h["n"], dtype=np.float32)
    title_hit = np.zeros(h["n"], dtype=np.float32)
    touched = False
    for t in uniq:
        e = h["post"].get(t)
        if e is not None:
            hit[e[:, 0]] += 1.0
            touched = True
        te = h["title_post"].get(t)
        if te is not None:
            title_hit[te] += 1.0
    if not touched:
        return []
    rows = candidate_rows(filters)
    if rows is not None:
        if len(rows) == 0:
            return []
        keep = np.zeros(h["n"], dtype=bool)
        keep[rows] = True
        hit = np.where(keep, hit, 0.0)
        title_hit = np.where(keep, title_hit, 0.0)
    score = (hit + boost * title_hit) / float(len(uniq))
    nz = np.flatnonzero(score)
    if len(nz) == 0:
        return []
    k = min(int(top_k), len(nz))
    picked = nz[np.argsort(-score[nz], kind="stable")[:k]]
    return [{"chunk_id": h["ids"][int(r)], "score_keyword": float(score[r]),
             "coverage": float(hit[r] / len(uniq)), "title_hits": int(title_hit[r])} for r in picked]


def lookup(chunk_ids):
    """按 id 取正文与元数据（内存查表）。"""
    h = _HOT
    out = {}
    for cid in chunk_ids:
        r = h["row"].get(cid)
        if r is not None:
            out[cid] = {"text": h["text"][r], "metadata": h["meta"][r]}
    return out
