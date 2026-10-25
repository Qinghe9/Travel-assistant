# -*- coding: utf-8 -*-
"""Agent SQLite 连接的统一并发参数。"""

from __future__ import annotations


def tune_sqlite(conn) -> None:
    """启用 WAL 与等待窗口，降低同步/异步连接并发写锁冲突。"""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=NORMAL")
