# -*- coding: utf-8 -*-
"""构建索引：向量化 + Chroma 集合 + BM25 索引 + IndexManifest。"""

from __future__ import annotations

import json
import pickle
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from app.rag import config as cfg
from app.rag.indexing.embed import Embedder


def load_chunks(path: Path | None = None):
    p = path or cfg.CHUNKS_FILE
    with p.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _clean_meta(c: dict) -> dict:
    m = {}
    for k in cfg.METADATA_FIELDS:
        v = c.get(k)
        m[k] = "" if v is None else v
    return m


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=str(cfg.ROOT), stderr=subprocess.DEVNULL).decode().strip()
    except Exception:  # noqa: BLE001
        return "nogit"


def get_client():
    import chromadb
    if cfg.CHROMA_MODE == "http":
        return chromadb.HttpClient(host=cfg.CHROMA_HOST, port=cfg.CHROMA_PORT)
    Path(cfg.CHROMA_PATH).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=cfg.CHROMA_PATH)


def _require_deps():
    missing = []
    for mod, pkg in (("chromadb", "chromadb"), ("jieba", "jieba"), ("rank_bm25", "rank-bm25")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise SystemExit("缺少依赖：%s\n请先安装：pip install -r requirements.txt\n"
                         "（注意：要在你运行脚本的那个 Python 环境里装）" % "、".join(missing))


def build_index(rebuild: bool = False, limit: int | None = None, verbose: bool = True) -> dict:
    _require_deps()
    t0 = time.time()
    chunks = load_chunks()
    if limit:
        chunks = chunks[:limit]
    if not chunks:
        raise RuntimeError("chunks.jsonl 为空，请先运行 scripts/run_clean.py 与 scripts/run_split.py")
    if verbose:
        print("载入 chunk %d 个" % len(chunks))

    emb = Embedder(verbose=verbose)
    vecs = emb.embed_texts([c["text"] for c in chunks])
    dim = len(vecs[0])
    if verbose:
        print("向量维度 %d | 模型 %s | 降级=%s" % (dim, emb.used_model, emb.degraded))

    client = get_client()
    if rebuild:
        try:
            client.delete_collection(cfg.COLLECTION)
            if verbose:
                print("已删除旧集合 %s" % cfg.COLLECTION)
        except Exception:  # noqa: BLE001
            pass
    col = client.get_or_create_collection(name=cfg.COLLECTION, metadata={"hnsw:space": "cosine"})

    B = 256
    for i in range(0, len(chunks), B):
        part = chunks[i:i + B]
        col.upsert(
            ids=[c["chunk_id"] for c in part],
            documents=[c["text"] for c in part],
            metadatas=[_clean_meta(c) for c in part],
            embeddings=vecs[i:i + B],
        )
        if verbose and (i // B) % 5 == 0:
            print("  写入 Chroma %d/%d" % (min(i + B, len(chunks)), len(chunks)), flush=True)

    import jieba
    from rank_bm25 import BM25Okapi
    jieba.initialize()
    tokens = [list(jieba.cut(c["text"])) for c in chunks]
    bm25 = BM25Okapi(tokens, k1=cfg.BM25_K1, b=cfg.BM25_B)
    cfg.INDEX.mkdir(parents=True, exist_ok=True)
    with cfg.BM25_FILE.open("wb") as fh:
        pickle.dump({"ids": [c["chunk_id"] for c in chunks], "tokens": tokens,
                     "bm25": bm25, "k1": cfg.BM25_K1, "b": cfg.BM25_B}, fh)

    manifest = {
        "index_version": "v1-%s-%d" % (datetime.now().strftime("%Y%m%d%H%M"), len(chunks)),
        "data_snapshot_date": "2026-10-11",
        "doc_count": len({c["parent_doc_id"] for c in chunks}),
        "chunk_count": len(chunks),
        "embedding_model": emb.used_model,
        "embedding_dim": dim,
        "degraded": emb.degraded,
        "collection": cfg.COLLECTION,
        "chroma_mode": cfg.CHROMA_MODE,
        "chunk_size": cfg.CHUNK_SIZE,
        "overlap": cfg.CHUNK_OVERLAP,
        "bm25_params": {"k1": cfg.BM25_K1, "b": cfg.BM25_B, "tokenizer": "jieba"},
        "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "build_seconds": round(time.time() - t0, 1),
        "git_commit": git_commit(),
    }
    cfg.INDEX.mkdir(parents=True, exist_ok=True)
    cfg.MANIFEST_FILE.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        print("IndexManifest 已写入 %s" % cfg.MANIFEST_FILE)
    return manifest
