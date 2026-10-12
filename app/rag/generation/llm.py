# -*- coding: utf-8 -*-
"""对话模型客户端（OpenAI 兼容），默认走硅基流动免费档。

环境变量（config.py 读取）：
    LLM_BASE_URL       默认硅基流动 https://api.siliconflow.cn/v1
    LLM_API_KEY        缺省回退到 RAG_EMBED_API_KEY（同供应商）；换 DeepSeek 时配 DEEPSEEK_API_KEY
    RAG_LLM_MODEL      默认 inclusionAI/Ling-mini-2.0（实测 p50 1.58s，引用 100% 合规）
    LLM_TIMEOUT        默认 8 秒
    LLM_MAX_RETRY      默认 1 次重试
    LLM_RETRY_BACKOFF  默认 0.4 秒（端到端预算 2 秒，不能用指数退避）
    LLM_TEMPERATURE    默认 0.2；接地作答时调用方显式传 0.0
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from app.rag import config as cfg


class LLMError(RuntimeError):
    def __init__(self, code, message, retriable=False):
        super().__init__("%s: %s" % (code, message))
        self.code = code
        self.message = message
        self.retriable = retriable


def chat(messages, *, model=None, temperature=None, max_tokens=None, timeout=None, retries=None):
    """调用对话模型。返回 {text, model, usage, latency_ms, attempts}；失败抛 LLMError。"""
    if not cfg.LLM_API_KEY:
        raise LLMError("no_api_key", "缺少 DEEPSEEK_API_KEY，请在 .env 里配置", retriable=False)
    model = model or cfg.LLM_MODEL
    temperature = cfg.LLM_TEMPERATURE if temperature is None else temperature
    max_tokens = max_tokens or cfg.LLM_MAX_TOKENS
    timeout = timeout or cfg.LLM_TIMEOUT
    retries = cfg.LLM_MAX_RETRY if retries is None else retries

    url = cfg.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature,
                       "max_tokens": max_tokens, "stream": False}).encode("utf-8")

    last = None
    for attempt in range(1, retries + 2):
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer %s" % cfg.LLM_API_KEY})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
            msg = d["choices"][0]["message"]
            return {"text": (msg.get("content") or "").strip(),
                    "reasoning": (msg.get("reasoning_content") or "").strip(),
                    "model": d.get("model", model), "usage": d.get("usage", {}),
                    "latency_ms": int((time.perf_counter() - t0) * 1000), "attempts": attempt}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            retriable = e.code in (429, 500, 502, 503, 504)
            last = LLMError("http_%s" % e.code, detail, retriable)
            if not retriable:
                break
        except Exception as e:  # noqa: BLE001 - 超时/网络
            last = LLMError(type(e).__name__, str(e)[:200], True)
        if attempt <= retries:
            time.sleep(cfg.LLM_RETRY_BACKOFF)
    raise last or LLMError("unknown", "调用失败")
