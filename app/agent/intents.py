# -*- coding: utf-8 -*-
"""七类意图的定义、必填槽位、工具与检索的编排表。

这张表就是 PRD 第 3.1 节意图表 + 第 4.1 节流程图的代码化表达。
改需求时只改这里，节点代码不用动。
"""

from __future__ import annotations

from app.models.schemas import Intent

# 每类意图的必填槽位（缺失就追问，绝不猜测——PRD 第 3.1 节）
REQUIRED_SLOTS: dict[Intent, tuple[str, ...]] = {
    Intent.DESTINATION: ("destination",),
    Intent.ITINERARY: ("destination", "days"),
    Intent.TRANSPORT: ("origin", "destination_place"),
    Intent.HOTEL: ("destination",),
    Intent.FOOD: ("destination",),
    Intent.WEATHER: ("destination", "date"),
    Intent.CHITCHAT: (),
}

# 槽位的中文名，用于追问文案
SLOT_LABEL = {
    "destination": "目的地",
    "start_city": "出发城市",
    "target_city": "到达城市",
    "origin": "出发地",
    "destination_place": "到达地点",
    "date": "日期（例如\u201c明天\u201d或\u201c10月20日\u201d）",
    "days": "天数",
    "people": "出行人数",
    "budget": "预算",
}

# 每类意图要用哪些工具（PRD 第 3.2 节"工具与知识"列）
INTENT_TOOLS: dict[Intent, tuple[str, ...]] = {
    Intent.DESTINATION: ("poi",),
    Intent.ITINERARY: ("weather", "poi", "route", "holiday", "air_quality"),
    Intent.TRANSPORT: ("route", "transit", "transport"),
    Intent.HOTEL: ("hotel",),
    Intent.FOOD: ("food",),
    Intent.WEATHER: ("weather", "air_quality"),
    Intent.CHITCHAT: (),
}

# 哪些意图需要先检索知识库。
# 依据 PRD 第 6.4 节边界：只有工具能回答的实时问题（天气/票价/余票）
# 不许用知识库内容替代，所以天气与交通不走检索。
INTENT_NEEDS_RETRIEVAL: dict[Intent, bool] = {
    Intent.DESTINATION: True,
    Intent.ITINERARY: True,
    Intent.TRANSPORT: False,
    # 住宿优先走实时高德 POI；只有工具无结果时，图里的 fallback 才检索知识库。
    Intent.HOTEL: False,
    Intent.FOOD: True,
    Intent.WEATHER: False,
    Intent.CHITCHAT: False,
}


def required_of(intent: Intent, text: str = "") -> tuple[str, ...]:
    """必填槽位。交通类按出行方式动态判断：

    - 地铁/公交/怎么走：只要出发地和到达地点；
    - 高铁/火车/航班：额外要求日期（票价/车次必须带日期）。
    """
    if intent is Intent.TRANSPORT:
        req = list(REQUIRED_SLOTS[Intent.TRANSPORT])
        if any(w in (text or "") for w in ("高铁", "火车", "动车", "航班", "机票")):
            req.append("date")
        return tuple(req)
    return REQUIRED_SLOTS.get(intent, ())


def missing_slots(intent: Intent, slots: dict, text: str = "") -> list:
    """返回还缺哪些必填槽位（保持 REQUIRED_SLOTS 的顺序）。"""
    out = []
    for key in required_of(intent, text):
        value = (slots or {}).get(key)
        if key == "origin" and not value:
            value = (slots or {}).get("start_city")
        if key == "destination_place" and not value:
            value = (slots or {}).get("target_city")
        if value in (None, "", [], {}):
            out.append(key)
    return out


def route_target(intent: Intent, missing: list) -> str:
    """路由函数的返回值：ask / chat / retrieve / tools。

    对应 PRD 第 4.1 节的 route 节点：先判断要不要追问，再判断走知识库还是走工具，
    避免"该追问却硬答"和"该调工具却编答案"。
    """
    if intent is Intent.CHITCHAT:
        return "chat"
    if missing:
        return "ask"
    if INTENT_NEEDS_RETRIEVAL.get(intent, True):
        return "retrieve"
    return "tools"
