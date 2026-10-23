# -*- coding: utf-8 -*-
"""Agent 层配置。

设计取舍：复用 app.rag.config 已经解析好的 .env，而不是再写一套配置读取。
仓库里已经有 DEEPSEEK_* / LLM_* / RAG_* 一整套参数，再造一份会出现
"两个地方都能改、互相不一致"的问题。本文件只补充 Agent 自己的少数开关。
"""

from __future__ import annotations

import os
from pathlib import Path

from app.rag import config as rag

ROOT = rag.ROOT
DATA = rag.DATA

# 对话模型（与 RAG 层共用同一份 DeepSeek 配置）
LLM_BASE_URL = rag.LLM_BASE_URL
LLM_API_KEY = rag.LLM_API_KEY
LLM_MODEL = rag.LLM_MODEL
LLM_TIMEOUT = rag.LLM_TIMEOUT
LLM_MAX_RETRY = rag.LLM_MAX_RETRY
LLM_TEMPERATURE = rag.LLM_TEMPERATURE
LLM_MAX_TOKENS = rag.LLM_MAX_TOKENS

# 意图识别只需要很短的结构化输出
CLASSIFY_MAX_TOKENS = int(os.environ.get("AGENT_CLASSIFY_MAX_TOKENS", "256"))
CLASSIFY_TIMEOUT = float(os.environ.get("AGENT_CLASSIFY_TIMEOUT", "6"))

# 检索（复用 RAG 层）
TOP_K_FINAL = rag.TOP_K_FINAL

# 记忆：sqlite=跨进程保留（验收要求）；memory=仅进程内，便于测试
MEMORY_MODE = os.environ.get("AGENT_MEMORY", "sqlite").strip().lower()
DB_PATH = Path(os.environ.get("AGENT_DB_PATH", str(DATA / "agent" / "agent.db")))
# 长期偏好库（跨会话，按 user_id 存）
PROFILE_DB_PATH = Path(os.environ.get("AGENT_PROFILE_DB", str(DATA / "agent" / "profile.db")))
# 日期槽位的有效期：超过这么多小时没有再次表达，就清空，避免拿旧日期排新行程
DATE_TTL_HOURS = float(os.environ.get("AGENT_DATE_TTL_HOURS", "24"))

# —— JevK5 结构化决策（意图路由）——
JEV_APP_KEY = os.environ.get("JEV_APP_KEY", "").strip()
JEV_MODEL = os.environ.get("JEV_MODEL", "JevK5-V0.3")
JEV_MODE = os.environ.get("JEV_MODE", "gateway").strip().lower()   # gateway | native
JEV_TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "5"))
# 低于该置信度不采纳 Jev 的路由结果，回退主 LLM/规则
JEV_MIN_CONFIDENCE = float(os.environ.get("JEV_MIN_CONFIDENCE", "0.55"))

# 轨迹落盘（PRD 可追溯要求：每次回答都能复盘）
TRACE_LOG = Path(os.environ.get("AGENT_TRACE_LOG", str(DATA / "agent" / "traces.jsonl")))
TRACE_ENABLED = os.environ.get("AGENT_TRACE_ENABLED", "1") not in ("0", "false", "False")

# 多轮：保留多少轮对话进模型上下文
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "10"))

# 无密钥时是否允许规则式降级（意图用规则、答案用抽取式）。
# 这不是 mock：降级路径同样不许编造事实，只是不调用大模型。
ALLOW_RULE_FALLBACK = os.environ.get("AGENT_ALLOW_RULE_FALLBACK", "1") not in ("0", "false", "False")
