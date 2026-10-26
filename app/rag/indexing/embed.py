# -*- coding: utf-8 -*-
"""向量化：BGE-M3 API 优先 + 本地 bge-small-zh-v1.5 兜底 + content_hash 永久缓存。

环境变量（config.py 读取）：
    RAG_EMBED_PROVIDER   api | local | auto（默认 auto：先 API，失败切本地并标记降级）
    RAG_EMBED_BASE_URL   OpenAI 兼容的 /embeddings 服务地址
    RAG_EMBED_API_KEY    对应密钥
    RAG_EMBED_MODEL      默认 BAAI/bge-m3
    RAG_EMBED_FALLBACK   本地兜底模型，默认 BAAI/bge-small-zh-v1.5
    RAG_EMBED_BATCH      批大小，默认 32
"""

from __future__ import annotations

import json
import sqlite3
import struct
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from app.rag import config as cfg

_HTTP_CLIENT = None


def _http_client():
    """复用到硅基流动的 TLS 连接，避免每次查询重新握手。"""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        import httpx
        _HTTP_CLIENT = httpx.Client(
            timeout=cfg.EMBED_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
        )
    return _HTTP_CLIENT


class Breaker:
    """熔断：60 秒窗口内连续 5 次失败 -> 打开 60 秒 -> 恢复只放行 1 个探测。"""

    def __init__(self, threshold=5, window=60, cooldown=60):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self.fails = 0
        self.opened_at = 0.0
        self.probing = False

    def allow(self) -> bool:
        if self.opened_at == 0:
            return True
        if time.time() - self.opened_at < self.cooldown:
            return False
        if self.probing:
            return False
        self.probing = True
        return True

    def ok(self):
        self.fails = 0
        self.opened_at = 0.0
        self.probing = False

    def fail(self):
        self.fails += 1
        self.probing = False
        if self.fails >= self.threshold:
            self.opened_at = time.time()


class EmbedCache:
    """以 content_hash 为键的永久向量缓存（sqlite，存 float32 blob）。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # LangGraph 的同步节点会在工作线程里跑，而 Embedder 是进程级单例：
        # 连接必须允许跨线程；同时用锁串行化 SQLite 访问（连接本身不保证并发安全）。
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.RLock()
        self.conn.execute("CREATE TABLE IF NOT EXISTS embed_cache ("
                          "content_hash TEXT, model TEXT, dim INTEGER, vec BLOB, "
                          "PRIMARY KEY (content_hash, model))")
        self.conn.commit()

    def get_many(self, keys, model):
        out, missing = {}, []
        with self._lock:
            cur = self.conn.cursor()
            for k in keys:
                row = cur.execute("SELECT dim, vec FROM embed_cache WHERE content_hash=? AND model=?",
                                  (k, model)).fetchone()
                if row:
                    out[k] = list(struct.unpack("<%df" % row[0], row[1]))
                else:
                    missing.append(k)
        return out, missing

    def put_many(self, model, items):
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO embed_cache (content_hash, model, dim, vec) VALUES (?,?,?,?)",
                [(k, model, len(v), struct.pack("<%df" % len(v), *v)) for k, v in items])
            self.conn.commit()

    def stats(self, model):
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) FROM embed_cache WHERE model=?",
                                    (model,)).fetchone()
            return row[0] if row else 0


class Embedder:
    def __init__(self, verbose=True):
        self.provider = cfg.EMBED_PROVIDER
        self.model = cfg.EMBED_MODEL
        # 建库用的是哪个模型：查询必须用同一个模型，否则向量空间不一致
        self.index_model = _index_model()
        self.fallback = cfg.EMBED_FALLBACK
        self.batch = cfg.EMBED_BATCH
        self.cache = EmbedCache(cfg.INDEX / "embed_cache.sqlite")
        self.degraded = False
        self.used_model = self.model
        self.breaker = Breaker()
        self._local = None
        self.verbose = verbose

    def embed_texts(self, texts):
        """返回与 texts 等长的向量列表；命中缓存的不重复调用模型。"""
        keys = [_sha1(t) for t in texts]
        cached, missing = self.cache.get_many(keys, self.model)
        if missing:
            todo = [t for t, k in zip(texts, keys) if k in set(missing)]
            self._log("  缓存命中 %d / 需向量化 %d" % (len(cached), len(todo)))
            vecs = self._embed_uncached(todo)
            self.cache.put_many(self.used_model, list(zip(missing, vecs)))
            cached.update(dict(zip(missing, vecs)))
        return [cached[k] for k in keys]

    def embed_query(self, query: str):
        """查询专用入口：规范化口语指代后再查缓存，提高多轮改写的缓存复用率。"""
        return self.embed_texts([normalize_query(query)])[0]

    @property
    def dim(self):
        return cfg.EMBED_DIM

    def _log(self, msg):
        if self.verbose:
            print(msg, flush=True)

    def _embed_uncached(self, texts):
        provider = self.provider
        if provider in ("api", "auto"):
            if self.breaker.allow():
                try:
                    vecs = self._embed_api(texts)
                    self.breaker.ok()
                    return vecs
                except Exception as exc:  # noqa: BLE001
                    self.breaker.fail()
                    self._log("  API 向量化失败：%s" % exc)
                    if provider == "api":
                        raise
                    if self.index_model and self.fallback != self.index_model:
                        raise RuntimeError(
                            "API 向量化失败（%s），而本地兜底模型 %s 与建库模型 %s 不一致。"
                            "混用两个模型会让查询向量和库向量不在同一空间，检索结果无意义。"
                            "请二选一：(1) 恢复 API 额度（索引无需重建）；"
                            "(2) 用 RAG_EMBED_PROVIDER=local 重建索引（维度变成 512）。"
                            % (exc, self.fallback, self.index_model)) from exc
            else:
                self._log("  熔断器打开，直接走本地兜底")
        return self._embed_local(texts)

    def _embed_api(self, texts):
        if not cfg.EMBED_BASE_URL or not cfg.EMBED_API_KEY:
            raise RuntimeError("缺少 RAG_EMBED_BASE_URL / RAG_EMBED_API_KEY")
        url = cfg.EMBED_BASE_URL.rstrip("/") + "/embeddings"
        out = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i:i + self.batch]
            body = json.dumps({"model": self.model, "input": chunk, "encoding_format": "float"}).encode("utf-8")
            last = None
            for attempt in range(1, 5):
                try:
                    response = _http_client().post(url, content=body, headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer %s" % cfg.EMBED_API_KEY})
                    response.raise_for_status()
                    data = response.json()
                    rows = sorted(data["data"], key=lambda x: x.get("index", 0))
                    out.extend([row["embedding"] for row in rows])
                    self._log("    批次 %d/%d 完成" % (i // self.batch + 1, (len(texts) + self.batch - 1) // self.batch))
                    last = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    if attempt < 4:
                        time.sleep(2 ** attempt)
            if last is not None:
                raise last
        return out

    def _embed_local(self, texts):
        if self._local is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # noqa: BLE001
                raise RuntimeError(
                    "本地兜底需要 sentence-transformers：pip install sentence-transformers") from exc
            self._log("  加载本地模型 %s（首次会下载）" % self.fallback)
            self._local = SentenceTransformer(self.fallback)
        self.degraded = True
        self.used_model = self.fallback
        self._log("  !! 降级为本地模型 %s（degraded=true）" % self.fallback)
        return [v.tolist() for v in self._local.encode(texts, batch_size=self.batch,
                                                       normalize_embeddings=True, show_progress_bar=False)]


def _index_model():
    """读取 IndexManifest 里记录的建库模型（没有索引时返回 None）。"""
    try:
        return json.loads(cfg.MANIFEST_FILE.read_text(encoding="utf-8")).get("embedding_model")
    except Exception:  # noqa: BLE001
        return None


def _sha1(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_query(query: str) -> str:
    """将等价追问归一成稳定缓存键，不改动建库文档。"""
    import re
    q = (query or "").strip()
    q = re.sub(r"\b(?:请问|麻烦问下|我想知道)\b", "", q)
    q = re.sub(r"(那里|那边|这里|这边|它|这个|那个)(?=的|有|怎|开|门|价|地|$)", "", q)
    q = re.sub(r"\s*的(?=开放|门票|价格|地址|位置|交通|营业)", " ", q)
    q = re.sub(r"[呢吗吧啊呀？?！!，,。]+$", "", q)
    return re.sub(r"\s+", " ", q).strip()
