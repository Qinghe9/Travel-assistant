# -*- coding: utf-8 -*-
"""FastAPI 应用入口。

启动时做两件预热（参考课程第 24/25 节的踩坑）：
1. 建一次 Agent 图（含 SQLite checkpointer）；
2. 预热 RAG 热索引，避免第一个用户请求付冷启动。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent import config as cfg
from app.api.demo import router as demo_router
from app.api.routes import router
from app.rag import config as rag

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from app.agent.graph import get_async_agent
        await get_async_agent()
    except Exception:
        logger.exception("Agent 图初始化失败，服务继续启动")
    try:
        from app.rag.retrieval.recall import warmup
        await asyncio.to_thread(warmup)
    except Exception:
        logger.exception("RAG 预热失败，服务继续启动（检索将降级）")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="旅游规划助手 · Agent API",
        version="0.1.0",
        description="LangGraph 状态图 + RAG + 工具调用（PRD v1.2）",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(router, prefix="/api/v1")
    app.include_router(demo_router)

    @app.get("/health")
    def health():
        deps = {
            "llm_configured": bool(cfg.LLM_API_KEY),
            "amap_configured": bool(os.environ.get("AMAP_API_KEY", "").strip()),
            "jev_configured": bool(cfg.JEV_APP_KEY),
            "chunks_file": rag.CHUNKS_FILE.exists(),
            "index_manifest": rag.MANIFEST_FILE.exists(),
            "memory_mode": cfg.MEMORY_MODE,
        }
        ready = deps["index_manifest"] and deps["llm_configured"]
        return {"status": "ok" if ready else "degraded", "deps": deps}

    return app


app = create_app()
