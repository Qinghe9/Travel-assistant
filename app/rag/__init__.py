# -*- coding: utf-8 -*-
"""RAG 模块对外接口（流程文档 7.3）。Agent 层只调用 build_evidence。"""

from app.rag.retrieval.evidence import build_evidence # noqa: F401
from app.rag.retrieval.fuse import search # noqa: F401
from app.rag.indexing.build import build_index # noqa: F401

__all__ = ["build_index", "search", "build_evidence"]
