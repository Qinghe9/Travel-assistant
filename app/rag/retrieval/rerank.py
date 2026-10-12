# -*- coding: utf-8 -*-
"""重排序：调用硅基流动 /v1/rerank（默认 BAAI/bge-reranker-v2-m3）对融合候选精排。

设计取舍
--------
* 只重排融合后的前 RERANK_CANDIDATES 条（默认 20），端到端多 ~250ms（实测）；
* **任何失败都返回原顺序**（超时/额度/网络/返回异常），检索不中断，降级静默但可观测；
* 分数写回 items[i]["rerank_score"]，排序优先用它，缺失则保持原融合序；
* 阈值判定仍用重排前的余弦/RRF 分（语义不变），重排只负责"把对的排上来"。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.rag import config as cfg

STATS = {"calls": 0, "failures": 0, "docs": 0, "latency_ms": 0.0}


def reset_stats() -> None:
    for k in STATS:
        STATS[k] = 0 if k != "latency_ms" else 0.0


def available(enabled: bool = None) -> bool:
    """enabled=None 时看全局开关；显式传 True 可单次强制启用（供 A/B 与特定场景）。"""
    on = cfg.RERANK_ENABLED if enabled is None else enabled
    return bool(on and cfg.RERANK_BASE_URL and cfg.RERANK_API_KEY)


def rerank(query: str, items: list, top_n: int = None, timeout: int = None,
           enabled: bool = None) -> list:
    """就地写入 rerank_score，返回按重排分降序的新列表；失败时原样返回。"""
    if not items or not available(enabled):
        return items
    cand = items[: cfg.RERANK_CANDIDATES]
    docs = [(it.get("text") or "")[:1200] for it in cand]
    if not any(docs):
        return items
    payload = {
        "model": cfg.RERANK_MODEL,
        "query": query,
        "documents": docs,
        "top_n": top_n or len(docs),
        "return_documents": False,
    }
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(
            cfg.RERANK_BASE_URL.rstrip("/") + "/rerank",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % cfg.RERANK_API_KEY},
        )
        with urllib.request.urlopen(req, timeout=timeout or cfg.RERANK_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("results") or []
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError, OSError, TimeoutError) as exc:
        STATS["failures"] += 1
        STATS["latency_ms"] += (time.perf_counter() - t0) * 1000
        print("  [rerank] 降级（沿用融合序）：%s" % exc, flush=True)
        return items
    STATS["calls"] += 1
    STATS["docs"] += len(docs)
    STATS["latency_ms"] += (time.perf_counter() - t0) * 1000

    for row in rows:
        i = row.get("index")
        if isinstance(i, int) and 0 <= i < len(cand):
            cand[i]["rerank_score"] = float(row.get("relevance_score") or 0.0)
    # 有分的按分降序；没拿到分的按原融合序排在后面
    order = sorted(range(len(cand)), key=lambda i: (-(cand[i].get("rerank_score", -1.0)), i))
    if cfg.RERANK_MIN_SCORE > 0:
        out = [cand[i] for i in order if (cand[i].get("rerank_score") or 0.0) >= cfg.RERANK_MIN_SCORE]
        out += [it for it in cand if "rerank_score" not in it]
    else:
        out = [cand[i] for i in order]
    return out + items[len(cand):]
