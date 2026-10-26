# -*- coding: utf-8 -*-
"""工具层基础设施：统一返回形状、缓存、熔断、超时与错误兜底。

对应 PRD 第 7 章：所有适配器实现统一 Provider 接口，输出 ToolResult；
60 秒内连续 5 次失败则熔断 60 秒；恢复阶段只放行 1 个探测请求；
缓存只复用未超过供应商有效期的真实响应。

红线：任何失败都不许用编造值顶上，只能如实返回 unavailable。
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from app.models.schemas import ToolResult, ToolStatus, now_iso


class ToolCache:
    """极简 TTL 缓存：只按「同一组参数」复用，且只在有效期内复用。"""

    def __init__(self):
        self._data = {}

    def get(self, key):
        item = self._data.get(key)
        if not item:
            return None
        value, expire_at = item
        if time.time() >= expire_at:
            self._data.pop(key, None)
            return None
        return value

    def put(self, key, value, ttl: int):
        if ttl and ttl > 0:
            self._data[key] = (value, time.time() + ttl)

    def clear(self):
        self._data.clear()


class Breaker:
    """熔断器：窗口内连续失败达阈值即打开，冷却后只放行一个探测请求。"""

    def __init__(self, threshold: int = 5, window: int = 60, cooldown: int = 60):
        self.threshold, self.window, self.cooldown = threshold, window, cooldown
        self.fails = 0
        self.first_fail_at = 0.0
        self.opened_at = 0.0
        self.probing = False

    def allow(self) -> bool:
        if self.opened_at == 0:
            return True
        if time.time() - self.opened_at < self.cooldown:
            return False
        if self.probing:
            return False
        self.probing = True
        return True

    def ok(self):
        self.fails = 0
        self.first_fail_at = 0.0
        self.opened_at = 0.0
        self.probing = False

    def fail(self):
        now = time.time()
        if not self.first_fail_at or now - self.first_fail_at > self.window:
            self.first_fail_at = now
            self.fails = 0
        self.fails += 1
        self.probing = False
        if self.fails >= self.threshold:
            self.opened_at = now

    @property
    def opened(self) -> bool:
        return self.opened_at > 0


def make_ok(provider: str, data: dict, message: str = "", ttl: int = 0) -> ToolResult:
    """构造成功结果，并给出缓存到期时间。"""
    expire = None
    if ttl:
        expire = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() + ttl))
    return ToolResult(provider=provider, status=ToolStatus.OK, data=data,
                      message=message, fetched_at=now_iso(), expires_at=expire)


def make_unavailable(provider: str, reason: str, code: str = "unavailable") -> ToolResult:
    """服务不可用：必须如实告知，绝不返回假数据。"""
    return ToolResult(provider=provider, status=ToolStatus.UNAVAILABLE,
                      message=reason, error_code=code, fetched_at=now_iso())


class Provider(ABC):
    """所有外部数据源的统一接口。"""

    name = "provider"
    ttl = 0
    timeout = 5.0

    def __init__(self):
        self.breaker = Breaker()
        self.cache = ToolCache()

    @abstractmethod
    def fetch(self, **kwargs) -> ToolResult:
        """真正去取数据；实现方只关心成功路径，失败直接抛异常或用 make_unavailable。"""

    def cache_key(self, kwargs: dict) -> str:
        return "%s|%s" % (self.name, sorted((k, str(v)) for k, v in (kwargs or {}).items()))

    def run(self, **kwargs) -> ToolResult:
        """统一入口：缓存 -> 熔断 -> 调用 -> 计时 -> 记录成功/失败。"""
        key = self.cache_key(kwargs)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        if not self.breaker.allow():
            return make_unavailable(self.name, "%s 连续失败已熔断，请稍后重试" % self.name,
                                    "breaker_open")
        t0 = time.perf_counter()
        try:
            result = self.fetch(**kwargs)
        except Exception as exc:
            result = make_unavailable(self.name, "%s 调用失败：%s: %s"
                                      % (self.name, type(exc).__name__, str(exc)[:160]),
                                      "exception")
        result.latency_ms = int((time.perf_counter() - t0) * 1000)
        if result.status is ToolStatus.OK:
            self.breaker.ok()
            self.cache.put(key, result, self.ttl)
        else:
            self.breaker.fail()
        return result


def readable(result: ToolResult) -> str:
    """把工具结果渲染成可读文本，供证据块使用（键值逐行）。"""
    if result.status is not ToolStatus.OK:
        return "（%s 不可用：%s）" % (result.provider, result.message or result.error_code)
    lines = []
    for key, value in (result.data or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = "；".join("%s=%s" % (k, v) for k, v in value.items())
        lines.append("%s：%s" % (key, value))
    return "\n".join(lines)
