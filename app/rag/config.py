# -*- coding: utf-8 -*-
"""RAG 模块配置：路径、数据范围、许可口径与全部运行参数（环境变量优先，其次 .env）。"""

from __future__ import annotations

import json
import os as _os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
INDEX = DATA / "index"

SCOPE_FILE = DATA / "scope_cities.json"
CHINA_TITLES_FILE = DATA / "china_scope_titles.json"
WIKIVOYAGE_SNAPSHOT = "20261011"
WIKIVOYAGE_DUMP = RAW / "zhwikivoyage" / WIKIVOYAGE_SNAPSHOT / "zhwikivoyage-latest-pages-articles.xml.bz2"
GEONAMES_CN = RAW / "geonames-cn" / "20261011" / "CN.zip"
SOURCE_URL = "https://zh.wikivoyage.org/wiki/%s"
LICENSE = "CC BY-SA 4.0"

# H2/H3 小标题 -> category 枚举
CATEGORY_MAP = {
    "观光": "attraction", "景点": "attraction", "活动": "attraction", "see": "attraction", "do": "attraction",
    "用餐": "food", "饮食": "food", "eat": "food", "drink": "food",
    "住宿": "hotel", "sleep": "hotel",
    "抵达": "transport", "到达": "transport", "交通": "transport", "航空": "transport",
    "铁路": "transport", "客车": "transport", "公路": "transport", "周边": "transport",
    "购物": "tips", "了解": "tips", "安全": "tips", "医疗": "tips", "夜生活": "tips",
    "下一站": "tips", "城市": "tips",
}


def load_china_titles():
    """全国范围的条目标题（省级子树 ∪ 提及式并集）。"""
    return json.loads(CHINA_TITLES_FILE.read_text(encoding="utf-8"))


def load_scope():
    """返回 (城市名列表, 城市名 -> GeoNames 记录)。"""
    data = json.loads(SCOPE_FILE.read_text(encoding="utf-8"))
    meta = {c["name_zh"]: c for c in data["cities"]}
    return list(meta), meta


# ---------------- 运行参数（环境变量优先，其次 .env 文件） ----------------


def _load_dotenv():
    """支持项目根目录的 .env（避免把密钥写进命令历史）。.env 已在 .gitignore 中。"""
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        _os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _env(name, default=None):
    return _os.environ.get(name, default)


EMBED_PROVIDER = _env("RAG_EMBED_PROVIDER", "auto")          # api | local | auto
EMBED_BASE_URL = _env("RAG_EMBED_BASE_URL", "")
EMBED_API_KEY = _env("RAG_EMBED_API_KEY", "")
EMBED_MODEL = _env("RAG_EMBED_MODEL", "BAAI/bge-m3")
EMBED_FALLBACK = _env("RAG_EMBED_FALLBACK", "BAAI/bge-small-zh-v1.5")
EMBED_BATCH = int(_env("RAG_EMBED_BATCH", "32"))
EMBED_TIMEOUT = int(_env("RAG_EMBED_TIMEOUT", "120"))
EMBED_DIM = int(_env("RAG_EMBED_DIM", "1024"))

CHROMA_MODE = _env("RAG_CHROMA_MODE", "local")               # local | http
CHROMA_PATH = _env("RAG_CHROMA_PATH", str(INDEX / "chroma"))
CHROMA_HOST = _env("RAG_CHROMA_HOST", "localhost")
CHROMA_PORT = int(_env("RAG_CHROMA_PORT", "8002"))
COLLECTION = _env("RAG_COLLECTION", "travel_kb_v1")

CHUNK_SIZE = int(_env("RAG_CHUNK_SIZE", "450"))
CHUNK_OVERLAP = int(_env("RAG_CHUNK_OVERLAP", "80"))
TOP_K_VECTOR = int(_env("RAG_TOP_K_VECTOR", "10"))
TOP_K_BM25 = int(_env("RAG_TOP_K_BM25", "10"))
TOP_K_FINAL = int(_env("RAG_TOP_K_FINAL", "3"))
RRF_K = int(_env("RAG_RRF_K", "60"))
MIN_FUSED_SCORE = float(_env("RAG_MIN_FUSED_SCORE", "0.0"))
# 向量余弦相似度阈值：绝对值有语义（实测域内 0.53~0.76，域外 0.36~0.48）
MIN_VECTOR_SIM = float(_env("RAG_MIN_VECTOR_SIM", "0.50"))
BM25_K1 = float(_env("RAG_BM25_K1", "1.5"))
BM25_B = float(_env("RAG_BM25_B", "0.75"))
MAX_PER_PARENT = int(_env("RAG_MAX_PER_PARENT", "2"))

# 对话模型（DeepSeek）
LLM_BASE_URL = _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_API_KEY = _env("DEEPSEEK_API_KEY", "")
LLM_MODEL = _env("LLM_MODEL", "deepseek-chat")
LLM_TIMEOUT = int(_env("LLM_TIMEOUT", "15"))
LLM_MAX_RETRY = int(_env("LLM_MAX_RETRY", "1"))
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "1024"))

CHUNKS_FILE = PROCESSED / "chunks.jsonl"
MANIFEST_FILE = INDEX / "IndexManifest.json"
BM25_FILE = INDEX / "bm25.pkl"

METADATA_FIELDS = ["province", "city", "county", "category", "source_url", "license",
                   "retrieved_at", "updated_at", "parent_doc_id", "chunk_index", "lang",
                   "content_hash", "source_type", "doc_title", "title_path"]
FILTER_FIELDS = {"province", "city", "county", "category", "source_type"}
