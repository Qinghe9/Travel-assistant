# -*- coding: utf-8 -*-
"""会话记忆（checkpointer）。

对应 PRD FR-07：用 thread_id 保存已确认槽位与最近对话，容器重启后仍可恢复。
sqlite 模式把状态写进 data/agent/agent.db；缺依赖时自动退回内存模式并明确告警，
绝不静默假装持久化成功。
"""

from __future__ import annotations

from app.agent import config as cfg
from app.agent.sqlite_utils import tune_sqlite


def make_checkpointer(mode: str = None):
    """返回一个 checkpointer 实例。mode: sqlite | memory（默认取配置）。"""
    mode = (mode or cfg.MEMORY_MODE or "sqlite").lower()
    if mode == "sqlite":
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver
            cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(cfg.DB_PATH), check_same_thread=False)
            tune_sqlite(conn)
            saver = SqliteSaver(conn)
            saver._raw_conn = conn
            return saver
        except Exception as exc:
            print("  [memory] SQLite 记忆不可用（%s），本次回退到内存模式；"
                  "需要持久化请安装：pip install langgraph-checkpoint-sqlite" % exc)
    from langgraph.checkpoint.memory import InMemorySaver
    return InMemorySaver()


async def make_async_checkpointer(mode: str = None):
    """异步图专用的 checkpointer。

    为什么必须和同步版分开：
    - 同步的 SqliteSaver 不实现异步方法，`graph.ainvoke/astream` 会直接抛
      NotImplementedError: The SqliteSaver does not support async methods；
    - 反过来 AsyncSqliteSaver 也不支持同步 `graph.invoke`（CLI 用的正是同步版）。
    所以两条路径各用一个实例，指向同一个 SQLite 文件。
    """
    mode = (mode or cfg.MEMORY_MODE or "sqlite").lower()
    if mode == "sqlite":
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            cfg.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = await aiosqlite.connect(str(cfg.DB_PATH))
            for pragma in ("PRAGMA journal_mode=WAL", "PRAGMA busy_timeout=3000",
                           "PRAGMA synchronous=NORMAL"):
                await conn.execute(pragma)
            await conn.commit()
            saver = AsyncSqliteSaver(conn)
            saver._raw_conn = conn
            return saver
        except Exception as exc:
            print("  [memory] Async SQLite 记忆不可用（%s），本次回退到内存模式；"
                  "需要持久化请安装：pip install aiosqlite" % exc)
    from langgraph.checkpoint.memory import InMemorySaver
    return InMemorySaver()


def memory_report(mode: str = None) -> dict:
    """用于自检脚本：说明当前用的是什么记忆。"""
    mode = (mode or cfg.MEMORY_MODE or "sqlite").lower()
    return {"mode": mode, "db_path": str(cfg.DB_PATH), "trace_log": str(cfg.TRACE_LOG)}
