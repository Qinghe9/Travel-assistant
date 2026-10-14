# -*- coding: utf-8 -*-
"""DeepSeek 对话模型客户端（OpenAI 兼容、SSE 流式）。

环境变量（config.py 读取）：
    LLM_BASE_URL       默认读取 DEEPSEEK_BASE_URL
    LLM_API_KEY        默认读取 DEEPSEEK_API_KEY
    RAG_LLM_MODEL      默认读取 LLM_MODEL（deepseek-chat）
    LLM_TIMEOUT        默认 8 秒
    LLM_MAX_RETRY      默认 1 次重试
    LLM_RETRY_BACKOFF  默认 0.4 秒（端到端预算 2 秒，不能用指数退避）
    LLM_TEMPERATURE    默认 0.2；接地作答时调用方显式传 0.0
"""

from __future__ import annotations

import concurrent.futures
import json
import time
import urllib.error
import urllib.request

from app.rag import config as cfg

_POOL = None
_HTTP_CLIENT = None


def _http_client():
    """复用 DeepSeek HTTP/1.1 连接，减少 DNS/TLS 握手对 TTFT 的影响。"""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        import httpx
        _HTTP_CLIENT = httpx.Client(
            timeout=cfg.ANSWER_TIMEOUT,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10, keepalive_expiry=60),
            headers={"Authorization": "Bearer %s" % cfg.LLM_API_KEY,
                     "Content-Type": "application/json", "Accept": "text/event-stream"},
        )
    return _HTTP_CLIENT


def _pool() -> concurrent.futures.ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")
    return _POOL


def chat_within(messages, deadline: float, **kwargs):
    """硬墙钟版 chat：到点即抛 LLMError("deadline")，不等慢响应。

    为什么需要：urllib 的 timeout 只是**每次读**的超时，服务端慢速分段返回时可以远超设定值
    （实测 0.6 s 的改写预算被拖到 1.6 s，1.5 s 的生成预算被拖到 2.3 s）。
    这里用线程 + future.result(timeout=) 把**墙钟**兜住；后台线程会自然结束，不再影响本次返回。
    """
    kwargs.setdefault("timeout", max(deadline, 0.1))
    fut = _pool().submit(chat, messages, **kwargs)
    try:
        return fut.result(timeout=deadline)
    except concurrent.futures.TimeoutError:
        raise LLMError("deadline", "超过 %.2fs 墙钟预算" % deadline, retriable=False)


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


def chat_stream(messages, *, model=None, temperature=None, max_tokens=None, timeout=None,
                retries=None, on_token=None):
    """使用 OpenAI 兼容 SSE 真流式生成。

    返回值除完整文本外还包含 ttft_ms（收到第一个非空内容 token 的墙钟时间）和
    latency_ms（完整流结束时间）。on_token 可由 FastAPI/SSE 层直接转发给客户端。
    """
    if not cfg.LLM_API_KEY:
        raise LLMError("no_api_key", "缺少 LLM API Key，请在 .env 里配置", retriable=False)
    model = model or cfg.LLM_MODEL
    temperature = cfg.LLM_TEMPERATURE if temperature is None else temperature
    max_tokens = max_tokens or cfg.LLM_MAX_TOKENS
    timeout = timeout or cfg.ANSWER_TIMEOUT
    retries = cfg.LLM_MAX_RETRY if retries is None else retries
    url = cfg.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    # DeepSeek 的兼容接口支持 stream=true；不发送可选 stream_options，兼容不同服务版本。
    body = json.dumps({"model": model, "messages": messages, "temperature": temperature,
                       "max_tokens": max_tokens, "stream": True}).encode("utf-8")
    last = None
    for attempt in range(1, retries + 2):
        t0 = time.perf_counter()
        parts, reasoning, usage, response_model = [], [], {}, model
        ttft_ms = None
        try:
            with _http_client().stream("POST", url, content=body, timeout=timeout) as resp:
                resp.raise_for_status()
                for raw in resp.iter_lines():
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    event = json.loads(payload)
                    response_model = event.get("model") or response_model
                    if event.get("usage"):
                        usage = event["usage"]
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    token = delta.get("content") or ""
                    reason = delta.get("reasoning_content") or ""
                    if reason:
                        reasoning.append(reason)
                    if token:
                        if ttft_ms is None:
                            ttft_ms = int((time.perf_counter() - t0) * 1000)
                        parts.append(token)
                        if on_token:
                            on_token(token)
            if ttft_ms is None and parts:
                ttft_ms = int((time.perf_counter() - t0) * 1000)
            return {"text": "".join(parts).strip(), "reasoning": "".join(reasoning).strip(),
                    "model": response_model, "usage": usage, "ttft_ms": ttft_ms,
                    "latency_ms": int((time.perf_counter() - t0) * 1000), "attempts": attempt}
        except Exception as exc:  # noqa: BLE001 - HTTP/SSE 流可能在任意阶段中断
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status:
                retriable = status in (429, 500, 502, 503, 504)
                detail = getattr(exc.response, "text", "")[:300]
                last = LLMError("http_%s" % status, detail, retriable)
            else:
                retriable = True
                last = LLMError(type(exc).__name__, str(exc)[:200], True)
            if not retriable:
                break
        if attempt <= retries:
            time.sleep(cfg.LLM_RETRY_BACKOFF)
    raise last or LLMError("unknown", "流式调用失败")
