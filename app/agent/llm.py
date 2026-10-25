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
    return text


def _json_candidates(raw: str) -> list[str]:
    text = _extract_json(raw)
    candidates = []
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = text.find(opening), text.rfind(closing)
        if start >= 0 and end > start:
            candidates.append(text[start:end + 1])
    candidates.append(text)
    return list(dict.fromkeys(candidates))


def _try_parse(raw: str, schema):
    last_error = "模型返回的不是合法 JSON"
    for candidate in _json_candidates(raw):
        try:
            data = json.loads(candidate)
        except Exception as exc:
            last_error = "模型返回的不是合法 JSON：%s（%s）" % (
                candidate[:120] or "（空）", str(exc)[:80])
            continue
        try:
            return validate(schema, data), ""
        except Exception as exc:
            last_error = "字段校验失败：%s" % str(exc)[:160]
    return None, last_error


def chat_json(messages, schema, **kwargs):
    """调用模型并校验为 schema；返回 (对象, 错误原因)。失败时对象为 None。"""
    kwargs.setdefault("temperature", 0.0)
    kwargs.setdefault("max_tokens", cfg.CLASSIFY_MAX_TOKENS)
    try:
        res = _rag_llm.chat(messages, **kwargs)
    except Exception as exc:
        return None, error_of(exc)
    raw = res.get("text") or ""
    parsed, err = _try_parse(raw, schema)
    if parsed is not None:
        return parsed, ""

    repair_messages = list(messages) + [
        {"role": "assistant", "content": raw or "（空输出）"},
        {"role": "user", "content":
            "上面的输出未通过校验（%s）。只输出修正后的 JSON，"
            "不要解释、不要代码块。" % err[:160]},
    ]
    try:
        repaired = _rag_llm.chat(repair_messages, **kwargs)
    except Exception as exc:
        return None, "%s；自动修复调用失败：%s" % (err, error_of(exc))
    return _try_parse(repaired.get("text") or "", schema)
