# -*- coding: utf-8 -*-
"""Agent 层的大模型调用。

复用 app.rag.generation.llm（已有的 DeepSeek 客户端、SSE 流式、重试与硬墙钟预算），
不重复实现第二个客户端 —— 否则超时/重试策略会出现两套标准。
本文件只补两件事：可用性判断，以及「要求模型返回 JSON 并做字段校验」。
"""

from __future__ import annotations

import json
import re

from app.agent import config as cfg
from app.models.schemas import validate
from app.rag.generation import llm as _rag_llm


def available() -> bool:
    """是否配置了对话模型密钥。没配置时节点走规则式降级路径。"""
    return bool(cfg.LLM_API_KEY and cfg.LLM_BASE_URL)


def error_of(exc: Exception) -> str:
    if isinstance(exc, _rag_llm.LLMError):
        return "%s: %s" % (exc.code, exc.message)
    return "%s: %s" % (type(exc).__name__, exc)


def chat(messages, **kwargs):
    return _rag_llm.chat(messages, **kwargs)


def chat_stream(messages, **kwargs):
    return _rag_llm.chat_stream(messages, **kwargs)


def _extract_json(text: str) -> str:
    """从模型输出里取出 JSON：容忍 ```json 代码块与前后多余说明。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def chat_json(messages, schema, **kwargs):
    """调用模型并校验为 schema；返回 (对象, 错误原因)。失败时对象为 None。"""
    kwargs.setdefault("temperature", 0.0)
    kwargs.setdefault("max_tokens", cfg.CLASSIFY_MAX_TOKENS)
    try:
        res = _rag_llm.chat(messages, **kwargs)
    except Exception as exc:
        return None, error_of(exc)
    raw = _extract_json(res.get("text") or "")
    try:
        data = json.loads(raw)
    except Exception:
        return None, "模型返回的不是合法 JSON：%s" % (raw[:120] or "（空）")
    try:
        return validate(schema, data), ""
    except Exception as exc:
        return None, "字段校验失败：%s" % str(exc)[:160]
