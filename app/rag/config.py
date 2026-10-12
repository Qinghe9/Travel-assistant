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

# 对话模型（默认硅基流动免费模型，见下方 SF_*）
LLM_TIMEOUT = int(_env("LLM_TIMEOUT", "8"))
LLM_MAX_RETRY = int(_env("LLM_MAX_RETRY", "1"))
# 重试退避：端到端预算只有 2 秒，指数退避（2s/4s）会把 P95 直接推过线，这里用短退避
LLM_RETRY_BACKOFF = float(_env("LLM_RETRY_BACKOFF", "0.4"))
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.2"))
# 实测生成耗时与输出长度几乎成正比（免费档约 50 字/秒）：200 tokens 约 1.3~1.8s
LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "200"))

# ---------------- 生成与重排（默认全部走硅基流动免费模型） ----------------
# 硅基流动：https://cloud.siliconflow.cn —— 免费档即可用 bge-m3 / bge-reranker-v2-m3 / Qwen2.5-7B
SF_BASE_URL = _env("RAG_SF_BASE_URL", _env("RAG_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"))
SF_API_KEY = _env("RAG_SF_API_KEY", _env("RAG_EMBED_API_KEY", ""))

# 对话模型：默认硅基流动免费模型。
# 注意：密钥必须与 base_url 配套——曾因为 .env 里存在 DEEPSEEK_API_KEY 而把它发给了
# 硅基流动的 /chat/completions，直接 401。这里按 base_url 判断该用哪个 key。
# 换回 DeepSeek：设 LLM_BASE_URL=https://api.deepseek.com（LLM_API_KEY 或 DEEPSEEK_API_KEY 均可）
LLM_BASE_URL = _env("LLM_BASE_URL", SF_BASE_URL)
if _env("LLM_API_KEY"):
    LLM_API_KEY = _env("LLM_API_KEY")
elif "deepseek" in LLM_BASE_URL.lower():
    LLM_API_KEY = _env("DEEPSEEK_API_KEY", SF_API_KEY)
else:
    LLM_API_KEY = SF_API_KEY
# 实测（同一份 3 条证据的接地问答，硅基流动免费档）：
#   inclusionAI/Ling-mini-2.0   1.6s 引用全合规  ← 选它
#   Qwen/Qwen2.5-7B-Instruct    2.8s 合规
#   Qwen/Qwen2.5-14B-Instruct   3.4s 有 2 句引用不合法
#   THUDM/GLM-4-9B-0414         4.8s 合规
#   stepfun-ai/Step-3.5-Flash   4.0s 空输出；tencent/Hunyuan-A13B 19s；Qwen/Qwen3-8B 超时
LLM_MODEL = _env("RAG_LLM_MODEL", "inclusionAI/Ling-mini-2.0")
# 轻量辅助模型：查询改写（只输出一句问句，越短越快）
LLM_HELPER_MODEL = _env("RAG_LLM_HELPER_MODEL", "inclusionAI/Ling-mini-2.0")
LLM_HELPER_MAX_TOKENS = int(_env("RAG_LLM_HELPER_MAX_TOKENS", "32"))   # 改写只输出一句问句

# 重排序：bge-reranker-v2-m3 实测 ~255ms（3 篇），免费档可用
RERANK_ENABLED = _env("RAG_RERANK_ENABLED", "1") not in ("0", "false", "False", "")
RERANK_BASE_URL = _env("RAG_RERANK_BASE_URL", SF_BASE_URL)
RERANK_API_KEY = _env("RAG_RERANK_API_KEY", SF_API_KEY)
RERANK_MODEL = _env("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_CANDIDATES = int(_env("RAG_RERANK_CANDIDATES", "8"))   # 送进重排的候选数（越多越准也越慢）
RERANK_TIMEOUT = float(_env("RAG_RERANK_TIMEOUT", "1.2"))   # 超过就退回融合序，不拖垮 2 秒预算
RERANK_MIN_SCORE = float(_env("RAG_RERANK_MIN_SCORE", "0.0"))  # 低于此分视为无关（0=不启用）

# 第 3 路：关键词/标题精确匹配召回
KEYWORD_ENABLED = _env("RAG_KEYWORD_ENABLED", "1") not in ("0", "false", "False", "")
TOP_K_KEYWORD = int(_env("RAG_TOP_K_KEYWORD", "10"))
KEYWORD_TITLE_BOOST = float(_env("RAG_KEYWORD_TITLE_BOOST", "2.0"))
# RRF 权重：等权会让"只在一路出现"的候选稀释共识，关键词路实测要降权
KEYWORD_WEIGHT = float(_env("RAG_KEYWORD_WEIGHT", "1.0"))
# 关键词覆盖率门槛：低于此比例的候选不进融合（挡掉只命中一个通用词的噪声）
KEYWORD_MIN_COVERAGE = float(_env("RAG_KEYWORD_MIN_COVERAGE", "0.0"))

# 多轮对话
DIALOGUE_MAX_TURNS = int(_env("RAG_DIALOGUE_MAX_TURNS", "6"))
DIALOGUE_REWRITE = _env("RAG_DIALOGUE_REWRITE", "1") not in ("0", "false", "False", "")

# 接地校验（逐句蕴含检查）：默认开，失败自动降级
FAITHFULNESS_CHECK = _env("RAG_FAITHFULNESS_CHECK", "1") not in ("0", "false", "False", "")

# 生成超时：硅基流动免费档偶发单次 8~10 秒的尖刺。超时不再报"服务不可用"，
# 而是退化为**抽取式作答**（直接引用证据原文句 + 引用编号），保证 2 秒预算内一定有据可查。
# 2.5s：Ling-mini 自身 p50 1.58s / max 1.82s（10 次实测），卡到 1.5s 会把一半请求误判超时；
# 真正要挡的是免费档偶发的 8~10 秒尖刺。
ANSWER_TIMEOUT = float(_env("RAG_ANSWER_TIMEOUT", "2.5"))
# 多轮改写超时：超时就用原问句检索（大概率仍可召回）
DIALOGUE_REWRITE_TIMEOUT = float(_env("RAG_DIALOGUE_REWRITE_TIMEOUT", "1.5"))


CHUNKS_FILE = PROCESSED / "chunks.jsonl"
MANIFEST_FILE = INDEX / "IndexManifest.json"
BM25_FILE = INDEX / "bm25.pkl"

METADATA_FIELDS = ["province", "city", "county", "category", "source_url", "license",
                   "retrieved_at", "updated_at", "parent_doc_id", "chunk_index", "lang",
                   "content_hash", "source_type", "doc_title", "title_path"]
FILTER_FIELDS = {"province", "city", "county", "category", "source_type"}
