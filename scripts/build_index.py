#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令构建/重建向量与 BM25 索引。

用法示例：
 python scripts/build_index.py --rebuild
 python scripts/build_index.py --provider local
 python scripts/build_index.py --provider api --embed-base-url https://api.siliconflow.cn/v1 --embed-api-key sk-xxx
 python scripts/build_index.py --limit 200 # 先小样本试跑

注意：环境变量必须在导入配置前设置，因此本脚本先解析参数再导入 app.rag。
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser(description="构建 RAG 索引")
ap.add_argument("--rebuild", action="store_true", help="先删除旧集合再重建")
ap.add_argument("--limit", type=int, default=None, help="只处理前 N 个 chunk（试跑用）")
ap.add_argument("--provider", choices=["api", "local", "auto"], help="向量化方式")
ap.add_argument("--embed-base-url", help="OpenAI 兼容 /embeddings 服务地址")
ap.add_argument("--embed-api-key", help="API 密钥")
ap.add_argument("--embed-model", help="向量模型名，默认 BAAI/bge-m3")
ap.add_argument("--chroma-mode", choices=["local", "http"], help="local=本地持久化，http=独立容器")
ap.add_argument("--chroma-host"), ap.add_argument("--chroma-port", type=int)
ap.add_argument("--collection", help="集合名，默认 travel_kb_v1")
a = ap.parse_args()

if a.provider:
 os.environ["RAG_EMBED_PROVIDER"] = a.provider
if a.embed_base_url:
 os.environ["RAG_EMBED_BASE_URL"] = a.embed_base_url
if a.embed_api_key:
 os.environ["RAG_EMBED_API_KEY"] = a.embed_api_key
if a.embed_model:
 os.environ["RAG_EMBED_MODEL"] = a.embed_model
if a.chroma_mode:
 os.environ["RAG_CHROMA_MODE"] = a.chroma_mode
if a.chroma_host:
 os.environ["RAG_CHROMA_HOST"] = a.chroma_host
if a.chroma_port:
 os.environ["RAG_CHROMA_PORT"] = str(a.chroma_port)
if a.collection:
 os.environ["RAG_COLLECTION"] = a.collection

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg # noqa: E402
from app.rag.indexing.build import build_index # noqa: E402

print("数据源 ：%s" % cfg.CHUNKS_FILE)
print("向量化 ：provider=%s model=%s batch=%d" % (
 cfg.EMBED_PROVIDER, cfg.EMBED_MODEL, cfg.EMBED_BATCH))
print("向量库 ：mode=%s collection=%s" % (cfg.CHROMA_MODE, cfg.COLLECTION))
print("-" * 56)
m = build_index(rebuild=a.rebuild, limit=a.limit)
print("-" * 56)
for k, v in m.items():
 print("%-18s %s" % (k, v))
