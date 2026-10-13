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

# 对话模型（DeepSeek）；embedding / rerank 仍使用硅基流动，凭据严格分离。
LLM_TIMEOUT = int(_env("LLM_TIMEOUT", "8"))
LLM_MAX_RETRY = int(_env("LLM_MAX_RETRY", "1"))
# 重试退避：端到端预算只有 2 秒，指数退避（2s/4s）会把 P95 直接推过线，这里用短退避
LLM_RETRY_BACKOFF = float(_env("LLM_RETRY_BACKOFF", "0.4"))
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.2"))
# 2 秒指标只约束首 token，不再为了完整响应时延强行截断答案。
LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "256"))

# ---------------- 模型服务 ----------------
# 硅基流动只负责 embedding / rerank。
SF_BASE_URL = _env("RAG_SF_BASE_URL", _env("RAG_EMBED_BASE_URL", "https://api.siliconflow.cn/v1"))
SF_API_KEY = _env("RAG_SF_API_KEY", _env("RAG_EMBED_API_KEY", ""))

# 对话与辅助改写默认都走 DeepSeek。LLM_* 显式配置仍可覆盖。
LLM_BASE_URL = _env("LLM_BASE_URL", _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
LLM_API_KEY = _env("LLM_API_KEY", _env("DEEPSEEK_API_KEY", ""))
LLM_MODEL = _env("RAG_LLM_MODEL", _env("LLM_MODEL", "deepseek-chat"))
LLM_HELPER_MODEL = _env("RAG_LLM_HELPER_MODEL", LLM_MODEL)
LLM_HELPER_MAX_TOKENS = int(_env("RAG_LLM_HELPER_MAX_TOKENS", "32"))   # 改写只输出一句问句
# 每段证据的压缩预算。answer.py 会按查询相关性选句，不再截取固定前缀。
EVIDENCE_MAX_CHARS = int(_env("RAG_EVIDENCE_MAX_CHARS", "900"))
EVIDENCE_TOTAL_CHARS = int(_env("RAG_EVIDENCE_TOTAL_CHARS", "1600"))

# 重排序：bge-reranker-v2-m3 实测 ~255ms（3 篇），免费档可用
# 默认关闭：实测在**带过滤的产品路径**上重排零增益（不重排的基线 Top3 命中率已是 1.000，
# MRR 还更高 0.9602 vs 0.9516），却要多花 ~300ms 并引入一个网络故障点。
# 只有"无过滤(open)"场景才有价值（Recall@3 0.871→0.923，且需 RERANK_CANDIDATES>=20）；
# 需要时用 RAG_RERANK_ENABLED=1 打开。
RERANK_ENABLED = _env("RAG_RERANK_ENABLED", "0") not in ("0", "false", "False", "")
RERANK_BASE_URL = _env("RAG_RERANK_BASE_URL", SF_BASE_URL)
RERANK_API_KEY = _env("RAG_RERANK_API_KEY", SF_API_KEY)
RERANK_MODEL = _env("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
RERANK_CANDIDATES = int(_env("RAG_RERANK_CANDIDATES", "6"))   # 送进重排的候选数（越多越准也越慢）
RERANK_TIMEOUT = float(_env("RAG_RERANK_TIMEOUT", "1.2"))   # 超过就退回融合序，不拖垮 2 秒预算
RERANK_MIN_SCORE = float(_env("RAG_RERANK_MIN_SCORE", "0.0"))  # 低于此分视为无关（0=不启用）

# 第 3 路：关键词/标题精确匹配召回
KEYWORD_ENABLED = _env("RAG_KEYWORD_ENABLED", "1") not in ("0", "false", "False", "")
TOP_K_KEYWORD = int(_env("RAG_TOP_K_KEYWORD", "10"))
KEYWORD_TITLE_BOOST = float(_env("RAG_KEYWORD_TITLE_BOOST", "2.0"))
# RRF 权重：等权会让"只在一路出现"的候选稀释共识，关键词路实测要降权
KEYWORD_WEIGHT = float(_env("RAG_KEYWORD_WEIGHT", "1.0"))
# 城市条目优先：查询指定城市时，给"来自该城市自身条目"（doc_title == city）的 chunk 加一点分。
# 否则省级文档（广东）的概述/行政区划段落会挤掉城市条目（广州）的景点内容。
CITY_DOC_BOOST = float(_env("RAG_CITY_DOC_BOOST", "0.012"))
# 关键词覆盖率门槛：低于此比例的候选不进融合（挡掉只命中一个通用词的噪声）
KEYWORD_MIN_COVERAGE = float(_env("RAG_KEYWORD_MIN_COVERAGE", "0.0"))
# 完整专名（如“寻甸汽车客运站”）命中正文时的确定性加分，避免被多路通用词共识挤出 Top3。
EXACT_PHRASE_BOOST = float(_env("RAG_EXACT_PHRASE_BOOST", "0.04"))

# 多轮对话
DIALOGUE_MAX_TURNS = int(_env("RAG_DIALOGUE_MAX_TURNS", "6"))
# 追问改写模式：auto=先用 LLM（有预算）超时回落规则式 | llm=只用 LLM | rule=只用规则式(零延迟) | off=不改写
# 实测规则式（把继承的城市拼进问句）与 LLM 改写的检索信号等价（余弦差 <0.005、Top3 大量重合），
# 所以 DIALOGUE_REWRITE=rule 可以把追问的 ~0.5s 改写开销直接省掉。
# 默认 rule（规则式改写：把继承的城市拼进问句，零延迟）。
# 实测（yolo 环境，8 单轮 + 5 轮追问）：auto 的兜底 6/13、P95 2022ms；
# rule 的兜底 4/13、P95 1931ms，而检索结果等价（余弦差 <0.005、Top3 大量重合）。
# 想要更"漂亮"的改写展示可以设 auto/llm，代价是每轮多花 0.3~0.5s 生成预算。
DIALOGUE_REWRITE = _env("RAG_DIALOGUE_REWRITE", "rule")

# 接地校验（逐句蕴含检查）：默认开，失败自动降级
FAITHFULNESS_CHECK = _env("RAG_FAITHFULNESS_CHECK", "1") not in ("0", "false", "False", "")

# 完整响应只受正常网络超时保护；2 秒门禁改由流式请求的 TTFT 指标承担。
ANSWER_TIMEOUT = float(_env("RAG_ANSWER_TIMEOUT", "30"))
TTFT_GATE_S = float(_env("RAG_TTFT_GATE_S", "2.0"))
# 多轮改写超时：超时就用原问句检索（大概率仍可召回）
# 0.6s：超过就用规则式改写（检索等价、零延迟），别让一次改写吃掉 2 秒预算的三分之一
DIALOGUE_REWRITE_TIMEOUT = float(_env("RAG_DIALOGUE_REWRITE_TIMEOUT", "0.3"))
# 接地校验：句子实词在**被引证据**里的覆盖率低于该值即判为编造、整句剔除；
# 另外句中的数字（票价/时刻/电话）必须逐字出现在证据里。
# 0.35：词面覆盖对"合法复述"天然偏低（实测 0.33 的句子其实有据），
# 所以覆盖率只做粗筛；真正的硬约束是"数字必须出现在证据里"。
GROUNDING_MIN_COVERAGE = float(_env("RAG_GROUNDING_MIN_COVERAGE", "0.35"))


CHUNKS_FILE = PROCESSED / "chunks.jsonl"
MANIFEST_FILE = INDEX / "IndexManifest.json"
BM25_FILE = INDEX / "bm25.pkl"

METADATA_FIELDS = ["province", "city", "county", "category", "source_url", "license",
                   "retrieved_at", "updated_at", "parent_doc_id", "chunk_index", "lang",
                   "content_hash", "source_type", "doc_title", "title_path"]
FILTER_FIELDS = {"province", "city", "county", "category", "source_type"}
