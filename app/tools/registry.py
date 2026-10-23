# -*- coding: utf-8 -*-
"""工具注册表：意图 -> 工具 -> 证据/引用。

工具结果统一转成「带 source_id 的证据条目」，与知识库证据同构，
这样 compose 节点只需要一套逐句绑定逻辑（PRD 第 6.4 节）。
"""

from __future__ import annotations

import hashlib
import inspect
import os

from app.agent.intents import INTENT_TOOLS
from app.models.schemas import Intent, ToolResult, ToolStatus

_INSTANCES = {}

# 槽位名（PRD 统一槽位模型）-> 工具参数名的候选顺序
SLOT_ALIASES = {
    "city": ("destination", "target_city", "city"),
    "destination": ("destination", "target_city"),
    "start_city": ("start_city",),
    "target_city": ("target_city",),
    "date": ("date", "start_date"),
    "end_date": ("end_date",),
    "entity": ("entity",),
    "days": ("days",),
    "people": ("people",),
    "budget": ("budget",),
    "category": ("category",),
}

# 工具的前置槽位：缺了就跳过，不制造"缺少参数"的无意义噪声
TOOL_REQUIREMENTS = {
    "weather": ("destination",),
    "poi": ("destination",),
    "route": ("start_city", "target_city"),
    "transport": ("start_city", "target_city", "date"),
    "train": ("start_city", "target_city", "date"),
    "hotel": ("destination",),
}


def get_provider(name: str):
    """按名字取 Provider 实例（懒加载 + 单例，保证缓存与熔断状态复用）。"""
    if name in _INSTANCES:
        return _INSTANCES[name]
    obj = None
    amap = bool(os.environ.get("AMAP_API_KEY", "").strip())
    if name == "weather":
        if amap and os.environ.get("WEATHER_PROVIDER", "open-meteo").lower() == "amap":
            from app.tools.amap import AmapWeather
            obj = AmapWeather()
        else:
            from app.tools.weather import OpenMeteoWeather
            obj = OpenMeteoWeather()
    elif name == "poi":
        if amap:
            from app.tools.amap import AmapPOI
            obj = AmapPOI()
        else:
            from app.tools.poi import OpenTripMapPOI
            obj = OpenTripMapPOI()
    elif name == "route":
        if amap:
            from app.tools.amap import AmapRoute
            obj = AmapRoute()
        else:
            from app.tools.route import OpenRouteService
            obj = OpenRouteService()
    elif name == "transport":
        from app.tools.transport import TravelportProvider
        obj = TravelportProvider("flight")
    elif name == "hotel":
        from app.tools.transport import TravelportProvider
        obj = TravelportProvider("hotel")
    _INSTANCES[name] = obj
    return obj


def _kwargs_for(provider, slots: dict) -> dict:
    """只把 Provider.fetch 真正接受的参数传进去，并做槽位名到参数名的映射。

    槽位层的字段是 destination（PRD 统一槽位模型），工具层用的是 city；
    没有这层映射，天气工具拿不到城市，只会以"缺少城市参数"失败。
    """
    try:
        params = inspect.signature(provider.fetch).parameters
    except (TypeError, ValueError):
        return {}
    slots = slots or {}
    out = {}
    for key in params:
        for candidate in SLOT_ALIASES.get(key, (key,)):
            value = slots.get(candidate)
            if value not in (None, "", [], {}):
                out[key] = value
                break
    return out


def run_for_intent(intent: Intent, slots: dict) -> list:
    """按意图表依次调用工具，返回 ToolResult 列表（失败的也返回，便于审计）。"""
    results = []
    for tool_name in INTENT_TOOLS.get(intent, ()):
        missing = [k for k in TOOL_REQUIREMENTS.get(tool_name, ()) if not (slots or {}).get(k)]
        if missing:
            continue
        provider = get_provider(tool_name)
        if provider is None:
            results.append(ToolResult(provider=tool_name, status=ToolStatus.UNAVAILABLE,
                                      message="未注册的工具：%s" % tool_name,
                                      error_code="not_registered"))
            continue
        results.append(provider.run(**_kwargs_for(provider, slots)))
    return results


def tool_source_id(provider: str, fetched_at: str) -> str:
    """工具证据的 source_id：sha1("tool:provider:时刻")[:12]。

    保持 12 位十六进制，与 RAG 层的引用校验正则兼容，
    这样 post_validate 不需要为工具证据写第二套规则。
    """
    raw = "tool:%s:%s" % (provider, fetched_at or "")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def to_evidence(results: list) -> list:
    """把成功的工具结果转成证据条目（失败的不进证据池，但要如实告知用户）。"""
    from app.tools.base import readable
    out = []
    for result in results:
        if result.status is not ToolStatus.OK:
            continue
        sid = tool_source_id(result.provider, result.fetched_at)
        out.append({
            "source_id": sid,
            "title": "工具：%s" % result.provider,
            "url": "",
            "text": readable(result),
            "score": 1.0,
            "updated_at": result.fetched_at,
            "source_type": "tool",
            "city": "",
            "kind": "tool",
            "metadata": {"provider": result.provider, "fetched_at": result.fetched_at,
                         "expires_at": result.expires_at},
        })
    return out


def to_citations(evidence: list) -> list:
    """工具证据的引用条目（与知识证据同样带 source_id）。"""
    out = []
    for item in evidence or []:
        md = item.get("metadata") or {}
        out.append({
            "source_id": item.get("source_id", ""),
            "title": item.get("title", ""),
            "url": "",
            "kind": "tool",
            "provider": md.get("provider", ""),
            "fetched_at": md.get("fetched_at", ""),
            "expires_at": md.get("expires_at"),
        })
    return out


def unavailable_notes(results: list) -> list:
    """给用户看的「哪些能力暂时不可用」提示（PRD 要求可解释，不静默失败）。"""
    return ["%s：%s" % (r.provider, r.message or r.error_code)
            for r in (results or []) if r.status is not ToolStatus.OK]
