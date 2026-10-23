# -*- coding: utf-8 -*-
"""长期记忆：跨会话、按用户的偏好（LangGraph Store）。

与短时记忆的分工：
- checkpointer（thread_id）：当前会话的历史与槽位；
- store（user_id）       ：跨会话的稳定偏好（口味、预算习惯等）。

红线（PRD 6.4）：长期记忆只能用作**偏好排序/追问参考**，
不能当作事实证据。事实仍必须绑定 source_id / tool_call_id。
"""

from __future__ import annotations

import sqlite3
import threading

from app.agent import config as cfg
from app.models.schemas import now_iso

NAMESPACE = ("user",)
PROFILE_FIELDS = ("preferences", "budget", "people")

_LOCK = threading.RLock()
_STORE = None
_CONN = None


def get_profile_store():
    """Store 单例：优先 SQLite 持久化，失败回退内存。"""
    global _STORE, _CONN
    if _STORE is not None:
        return _STORE
    try:
        from langgraph.store.sqlite import SqliteStore
        cfg.PROFILE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(cfg.PROFILE_DB_PATH), check_same_thread=False)
        store = SqliteStore(conn)
        store.setup()
        conn.commit()          # setup 会留下事务，不提交会导致后续 put 报
        _STORE, _CONN = store, conn
        return store
    except Exception as exc:
        print("  [memory] 长期记忆 SQLite 不可用（%s），回退到内存模式" % exc)
        from langgraph.store.memory import InMemoryStore
        _STORE = InMemoryStore()
        return _STORE


def _commit():
    if _CONN is not None:
        try:
            _CONN.commit()
        except Exception:
            pass


def _merge(current: dict, updates: dict) -> dict:
    """偏好取并集；标量字段以最新一次明确表达为准。"""
    out = {k: current.get(k) for k in PROFILE_FIELDS
           if current.get(k) not in (None, "", [], {})}
    prefs = list(out.get("preferences") or [])
    for item in (updates or {}).get("preferences") or []:
        if item and item not in prefs:
            prefs.append(item)
    if prefs:
        out["preferences"] = prefs
    for key in ("budget", "people"):
        value = (updates or {}).get(key)
        if value not in (None, "", [], {}):
            out[key] = value
    return out


def load_profile(user_id: str) -> dict:
    """读取用户长期偏好；任何异常都返回空 dict，不影响主流程。"""
    if not user_id:
        return {}
    try:
        with _LOCK:
            item = get_profile_store().get(NAMESPACE, user_id)
        return dict(item.value or {}) if item else {}
    except Exception as exc:
        print("  [memory] 读取长期偏好失败：%s" % exc)
        return {}


def save_profile(user_id: str, updates: dict) -> dict:
    """只保存本轮用户明确表达的偏好字段。"""
    if not user_id or not updates:
        return load_profile(user_id)
    try:
        with _LOCK:
            store = get_profile_store()
            item = store.get(NAMESPACE, user_id)
            current = dict(item.value or {}) if item else {}
            merged = _merge(current, updates)
            merged["updated_at"] = now_iso()
            store.put(NAMESPACE, user_id, merged)
            _commit()
        return merged
    except Exception as exc:
        print("  [memory] 写入长期偏好失败：%s" % exc)
        return load_profile(user_id)


def clear_profile(user_id: str) -> None:
    """清空某个用户的长期偏好（演示/测试用）。"""
    if not user_id:
        return
    try:
        with _LOCK:
            get_profile_store().put(NAMESPACE, user_id, {})
            _commit()
    except Exception as exc:
        print("  [memory] 清除长期偏好失败：%s" % exc)


def profile_hint(profile: dict) -> str:
    """给模型的偏好提示：明确写“不得作为事实依据”。"""
    if not profile:
        return ""
    parts = []
    if profile.get("preferences"):
        parts.append("偏好：" + "、".join(profile["preferences"][:6]))
    if profile.get("budget"):
        parts.append("习惯预算：%s 元" % profile["budget"])
    if profile.get("people"):
        parts.append("常同行人数：%s" % profile["people"])
    if not parts:
        return ""
    return ("用户长期偏好（仅用于排序与措辞，**不得作为事实依据**，也不要为它标注来源）："
            + "；".join(parts))
