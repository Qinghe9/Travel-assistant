# -*- coding: utf-8 -*-
"""LangGraph 节点实现。

对应 PRD 第 4.1 节的八个节点，外加两个分支节点：
    normalize_input -> classify_and_extract -> route
      -> ask_clarify（缺槽追问） / chat（闲聊兜底）
      -> retrieve（知识库） -> call_tools（工具） -> validate_plan -> compose -> persist

两条不变式：
1. **不当猜**：槽位缺失就追问，不替用户补日期、人数、预算；
2. **不编造**：答案里的每个事实句必须绑定 source_id；没有证据就说没有。
"""

from __future__ import annotations

import json
import re
import time
import datetime as _dt

from app.agent import config as cfg
from app.agent import jev, llm, prompts, retrieval
from app.agent import slots as slot_lib
from app.agent.intents import INTENT_NEEDS_RETRIEVAL, SLOT_LABEL, missing_slots, route_target
from app.agent.state import trace_event
from app.agent.store import (
    append_turn,
    load_history,
    load_profile,
    memory_hint,
    profile_hint,
    save_profile,
)
from app.models.schemas import (
    Intent,
    LLMIntentPayload,
    LLMQueryPlanPayload,
    ToolStatus,
    dump,
    now_iso,
)
from app.tools import registry

NO_EVIDENCE_ANSWER = "知识库中没有找到与这个问题相关的依据，无法作答。"

_INT_TOOL_OK = ToolStatus.OK.value


def _as_intent(value) -> Intent:
    try:
        return Intent(value)
    except Exception:
        return Intent.CHITCHAT


def _text_of(message) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def _elapsed_ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def date_is_stale(stamp: str, ttl_hours: float = None) -> bool:
    """日期槽位是否超过有效期。

    没有时间戳（老会话/直接注入的 state）一律视为过期：宁可多问一句，
    也不要拿一个来路不明的旧日期去排新行程。
    """
    ttl = cfg.DATE_TTL_HOURS if ttl_hours is None else ttl_hours
    if not stamp:
        return True
    try:
        last = _dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return True
    now = _dt.datetime.now(last.tzinfo) if last.tzinfo else _dt.datetime.now()
    return (now - last).total_seconds() > ttl * 3600


def _emit_token(token: str) -> None:
    """把模型增量文本推到自定义流里。

    只有 graph.astream(stream_mode 包含 custom) 时才有效；
    普通 invoke 下 get_stream_writer 不可用，这里安静跳过，不影响非流式调用。
    """
    if not token:
        return
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()({"type": "token", "content": token})
    except Exception:
        pass


def _history_text(state, turns: int = 2, answer_chars: int = 120) -> str:
    """最近几轮对话，用于意图识别与改写。

    只带 2 轮：省 token，也避免模型把更早轮次的主题带进当前问题。
    """
    msgs = list(state.get("messages") or [])[:-1]
    lines = []
    for message in msgs[-turns * 2:]:
        role = "用户" if (isinstance(message, dict) and message.get("role") == "user") else "助手"
        text = _text_of(message).replace("\n", " ").strip()
        if text:
            lines.append("%s：%s" % (role, text[:answer_chars]))
    return "\n".join(lines)


def _clean_slots(raw: dict) -> dict:
    """把大模型给的槽位收敛到白名单，并做范围校验（不合法就丢弃，不猜）。"""
    out = {}
    for key, value in (raw or {}).items():
        if key not in slot_lib.SLOT_FIELDS or value in (None, "", [], {}):
            continue
        if key in ("days", "people", "budget"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if key == "days" and not (1 <= value <= 14):
                continue
            if key == "people" and value < 1:
                continue
            if key == "budget" and value < 0:
                continue
        if key == "preferences":
            value = [str(v) for v in (value if isinstance(value, list) else [value])]
        if key in ("date", "start_date", "end_date"):
            # 模型很容易把“明天”编成某个具体年份，这里做硬校验：
            # 必须是合法 ISO 日期，且落在 [昨天, 一年后] 内。
            try:
                parsed = _dt.date.fromisoformat(str(value).strip())
            except ValueError:
                continue
            today = _dt.date.today()
            if not (today - _dt.timedelta(days=1) <= parsed <= today + _dt.timedelta(days=366)):
                continue
            value = parsed.isoformat()
        out[key] = value
    return out


def _rule_result(text: str) -> dict:
    return {
        "intent": slot_lib.rule_intent(text),
        "confidence": 0.0,
        "slots": _clean_slots(slot_lib.rule_extract(text)),
    }


def _llm_result(payload) -> dict:
    try:
        intent = Intent(payload.intent)
    except Exception:
        intent = slot_lib.rule_intent("")
    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, float(payload.confidence or 0.0))),
        "slots": _clean_slots(payload.slots or {}),
    }


_EXPLAIN_RE = re.compile(
    r"(为什么|为何|凭什么|依据|推荐理由|理由是什么|理由呢|怎么选|怎么挑|"
    r"这些.{0,8}(推荐|选择|结果)|解释一下)"
)


def _is_explain_followup(text: str, last_turn: dict) -> bool:
    """识别“为什么选这些/依据是什么”一类追问。

    只看很短的指代式追问，并要求上一轮已经形成答案。这样既能让解释
    直接复用上一轮证据，也不会把“为什么北京天气会降温”误当成本轮追问。
    """
    q = (text or "").strip()
    if not last_turn or not last_turn.get("answer") or not q or len(q) > 32:
        return False
    if not _EXPLAIN_RE.search(q):
        return False
    previous_city = ((last_turn.get("slots") or {}).get("destination")
                     or (last_turn.get("slots") or {}).get("target_city"))
    named_city = slot_lib.detect_city(q)
    if named_city and named_city != previous_city:
        return False
    return True


_REGENERATE_RE = re.compile(
    r"(重新回答|再回答|再说一遍|重新说|重新整理|换个说法|重述|重新生成|"
    r"重新组织|把上面.*(再|重新))"
)


def _last_turn_is_fresh(last_turn: dict, max_minutes: int = 10) -> bool:
    """重复问题只在短时间窗口内复用，避免实时天气等内容无限期旧答复用。"""
    stamp = (last_turn or {}).get("completed_at", "")
    if not stamp:
        return False
    try:
        completed = _dt.datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return False
    now = _dt.datetime.now(completed.tzinfo) if completed.tzinfo else _dt.datetime.now()
    return (now - completed).total_seconds() <= max_minutes * 60


def _same_query(last_turn: dict, text: str) -> bool:
    if not last_turn or not last_turn.get("answer"):
        return False
    previous = last_turn.get("normalized_query") or last_turn.get("query") or ""
    return slot_lib.normalize_text(previous) == slot_lib.normalize_text(text)


def _build_tasks_from_specs(candidates: list, previous: dict, profile: dict) -> tuple:
    """把 (intent, query, slots) 规划项整理成可执行任务，并补齐共享槽位。"""
    aggregate = {k: v for k, v in (previous or {}).items()
                 if k not in slot_lib.TRANSIENT_SLOTS}
    for _intent, _part, slots in candidates:
        aggregate = slot_lib.merge_slots(aggregate, slots, new_city=slots.get("destination"))
    shared_city = (aggregate.get("target_city") or aggregate.get("destination")
                   or (profile or {}).get("destination"))
    if shared_city and not aggregate.get("destination"):
        aggregate["destination"] = shared_city
    transport_tasks = [item for item in candidates if item[0] is Intent.TRANSPORT]
    if transport_tasks and shared_city:
        aggregate["target_city"] = shared_city
        aggregate["destination_place"] = shared_city
    if aggregate.get("start_city") and not aggregate.get("origin"):
        aggregate["origin"] = aggregate["start_city"]
    if aggregate.get("origin") and not aggregate.get("start_city"):
        known_city = slot_lib.detect_city(aggregate["origin"])
        if known_city:
            aggregate["start_city"] = known_city

    tasks = []
    intent_categories = {
        Intent.DESTINATION: "attraction",
        Intent.FOOD: "food",
        Intent.HOTEL: "hotel",
        Intent.TRANSPORT: "transport",
    }
    for intent, part, slots in candidates:
        shared_base = dict(aggregate)
        shared_base.pop("category", None)
        shared_base.pop("entity", None)
        task_slots = slot_lib.merge_slots(shared_base, slots, new_city=slots.get("destination"))
        if intent_categories.get(intent):
            task_slots["category"] = intent_categories[intent]
        if shared_city and not task_slots.get("destination"):
            task_slots["destination"] = shared_city
        if intent is Intent.TRANSPORT and shared_city:
            task_slots["target_city"] = task_slots.get("target_city") or shared_city
            task_slots["destination_place"] = task_slots.get("destination_place") or shared_city
        if intent is Intent.TRANSPORT and task_slots.get("start_city") and not task_slots.get("origin"):
            task_slots["origin"] = task_slots["start_city"]
        tasks.append({
            "query": part,
            "intent": intent.value,
            "slots": task_slots,
            "missing": missing_slots(intent, task_slots, part),
        })
    return tasks, aggregate


def _multi_intent_tasks(text: str, previous: dict, profile: dict) -> tuple:
    """规则兜底：拆分“交通？住宿？”这类复合请求。"""
    parts = slot_lib.split_subqueries(text)
    if len(parts) < 2:
        return [], {}
    candidates = []
    seen = set()
    for part in parts:
        intent = slot_lib.rule_intent(part)
        if intent is Intent.CHITCHAT or intent.value in seen:
            continue
        seen.add(intent.value)
        candidates.append((intent, part, slot_lib.rule_extract(part)))
    if len(candidates) < 2:
        return [], {}
    return _build_tasks_from_specs(candidates, previous, profile)


_COMPLEX_INTENT_MARKERS = {
    Intent.TRANSPORT: ("怎么去", "怎么走", "怎么从", "出发", "高铁", "火车", "飞机", "地铁", "公交"),
    Intent.WEATHER: ("天气", "气温", "下雨", "冷不冷", "热不热"),
    Intent.HOTEL: ("酒店", "住宿", "住哪", "住哪里", "哪里住", "宾馆"),
    Intent.FOOD: ("美食", "吃", "餐厅", "小吃", "火锅"),
    Intent.ITINERARY: ("规划", "行程", "安排几天", "几天游"),
    Intent.DESTINATION: ("景点", "好玩", "玩", "必去", "旅游", "旅行"),
}


def _looks_complex_query(text: str) -> bool:
    """判断是否需要让 LLM 做结构化多任务规划，而不是单意图快速路由。"""
    parts = slot_lib.split_subqueries(text)
    if len(parts) > 1:
        return True
    matched = 0
    for words in _COMPLEX_INTENT_MARKERS.values():
        if any(word in (text or "") for word in words):
            matched += 1
    return matched >= 2


def _llm_query_plan_tasks(payload, text: str, previous: dict, profile: dict) -> tuple:
    """把语言模型返回的结构化任务收敛成内部任务。"""
    if payload is None:
        return [], {}
    explicit_itinerary = any(
        word in (text or "") for word in ("规划", "行程", "安排", "几天", "日游", "路线")
    )
    candidates = []
    for raw in payload.tasks or []:
        try:
            intent = Intent(raw.intent)
        except Exception:
            continue
        if intent is Intent.CHITCHAT:
            continue
        part = slot_lib.normalize_text(raw.query or text)
        slots = _clean_slots(raw.slots or {})
        if intent is Intent.ITINERARY and not slots.get("days") and not explicit_itinerary:
            # 保护：用户只说“想去北京玩”时，不能自动升级成需要天数的行程规划。
            intent = Intent.DESTINATION
        candidates.append((intent, part, slots))
    if not candidates:
        return [], {}
    return _build_tasks_from_specs(candidates, previous, profile)


def _intents_of(state) -> list:
    values = state.get("intents") or [state.get("intent", "")]
    out = []
    for value in values:
        try:
            intent = Intent(value)
        except Exception:
            continue
        if intent is not Intent.CHITCHAT and intent not in out:
            out.append(intent)
    return out or [_as_intent(state.get("intent"))]


def _is_weather_provider(provider: str) -> bool:
    name = str(provider or "").lower()
    return name in ("open-meteo", "amap-weather", "weather") or "weather" in name


_DESTINATION_REFERENCE_WORDS = (
    "那里", "那边", "这个地方", "该地", "上述", "前面提到", "刚才提到",
)


def _has_destination_reference(text: str) -> bool:
    return any(word in (text or "") for word in _DESTINATION_REFERENCE_WORDS)


def _is_memory_recall_query(text: str) -> bool:
    asks_history = (
        any(w in text for w in ("之前", "以前", "上次", "历史记录", "历史对话",
                                "对话记录", "聊天记录", "保存的记录"))
        and any(w in text for w in ("记得", "知道", "去哪", "哪里", "目的地",
                                    "搜索", "查找", "查一下", "对话"))
    )
    asks_destination = (("想去哪" in text or "目的地" in text)
                        and ("记得" in text or "知道" in text))
    return bool(asks_history or asks_destination)


def _memory_recall_answer(user_id: str, profile: dict, text: str) -> str:
    """回答“你还记得我想去哪吗”；只读跨会话记忆，不访问工具/RAG。"""
    if not _is_memory_recall_query(text):
        return ""
    history = load_history(user_id)
    destinations = []
    for value in list((profile or {}).get("recent_destinations") or []):
        if value and value not in destinations:
            destinations.append(value)
    destination = (profile or {}).get("destination")
    if destination and destination not in destinations:
        destinations.insert(0, destination)
    if not destinations and not history:
        return "目前还没有保存到可用的跨会话旅行记录。你可以直接告诉我想去的城市。"
    destination_history = list((profile or {}).get("destination_history") or [])
    if not destinations:
        for row in reversed(history):
            city = ((row.get("slots") or {}).get("destination")
                    or slot_lib.detect_city(row.get("query") or ""))
            if city and city not in destinations:
                destinations.append(city)
    if destinations:
        if destination_history:
            latest = destination_history[0]
            older = [x.get("city") for x in destination_history[1:]
                     if x.get("city") and x.get("city") != latest.get("city")]
            answer = ("最近一次关注/计划去的是%s（%s）" % (
                latest.get("city"), latest.get("updated_at", "")[:19].replace("T", " ")))
            if older:
                answer += "；此前还关注过：%s" % "、".join(older[:5])
            return answer + "。目前已保存 %d 轮对话。" % len(history)
        return (
            "根据跨会话保存的对话记录，你最近关注/计划去的是：%s。"
            "目前已保存 %d 轮对话，所以新会话也可以延续这些上下文。"
            % ("、".join(destinations[:5]), len(history))
        )
    return "跨会话记录里暂时没有解析出明确目的地，但已经保存了 %d 轮对话。" % len(history)


# ---------------------------------------------------------------- 节点

def normalize_input(state):
    """取本轮问题并归一化；同时清空上一轮的临时字段。

    清空很重要：evidence / tool_results 这些不是 reducer 字段，
    不清就会在下一轮沿用过期证据，属于隐蔽的事实性错误。
    """
    t0 = time.perf_counter()
    msgs = state.get("messages") or []
    text = slot_lib.normalize_text(_text_of(msgs[-1])) if msgs else ""
    user_id = (state.get("user_id") or "demo").strip() or "demo"
    profile = load_profile(user_id)
    detail = text[:60]
    if profile.get("preferences"):
        detail += "｜长期偏好：" + "、".join(profile["preferences"][:4])
    return {
        "query": text,
        "normalized_query": "",
        "user_id": user_id,
        "profile": profile,
        "destination_source": "",
        "follow_up": "",
        "evidence": [],
        "citations": [],
        "tool_results": [],
        "evidence_meta": {},
        "plan": {},
        "answer": "",
        "no_evidence": False,
        "degraded": False,
        "evidence_binding_rate": 0.0,
        "trace": [trace_event("normalize_input", _elapsed_ms(t0), detail=detail)],
    }


def classify_and_extract(state):
    """FR-01：一次调用输出结构化意图与槽位；失败则规则降级（不是 mock）。"""
    t0 = time.perf_counter()
    text = state.get("query") or ""
    user_id = (state.get("user_id") or "demo").strip() or "demo"
    last_turn = dict(state.get("last_turn") or {})

    # 记忆回忆是元问题，不能套用普通旅行意图规则。
    if _is_memory_recall_query(text):
        profile_now = dict(state.get("profile") or {}) or load_profile(user_id)
        destination = profile_now.get("destination")
        slots_out = {"destination": destination} if destination else {}
        return {
            "intent": Intent.CHITCHAT.value,
            "intents": [Intent.CHITCHAT.value],
            "tasks": [],
            "confidence": 1.0,
            "slots": slots_out,
            "destination_source": "memory",
            "missing": [],
            "pending_intent": "",
            "pending_missing": [],
            "follow_up": "",
            "route_source": "memory",
            "normalized_query": text,
            "trace": [trace_event(
                "classify_and_extract", _elapsed_ms(t0),
                detail="识别为跨会话记忆回忆", source="memory",
                confidence=1.0, slots=slots_out, missing=[],
            )],
        }

    # 重述/重新回答：只复用上一轮最终答案和引用，绝不重跑工具与 RAG。
    if (_REGENERATE_RE.search(text or "") and last_turn.get("answer")
            and len(text) <= 40):
        prior_intent = _as_intent(last_turn.get("intent"))
        return {
            "intent": prior_intent.value,
            "intents": list(last_turn.get("intents") or [prior_intent.value]),
            "tasks": list(last_turn.get("tasks") or []),
            "confidence": 1.0,
            "slots": dict(last_turn.get("slots") or {}),
            "destination_source": last_turn.get("destination_source", ""),
            "missing": [],
            "pending_intent": "",
            "pending_missing": [],
            "follow_up": "regenerate",
            "route_source": "regenerate",
            "normalized_query": last_turn.get("normalized_query") or last_turn.get("query") or text,
            "trace": [trace_event(
                "classify_and_extract", _elapsed_ms(t0),
                detail="识别为重新回答，复用上一轮结果",
                source="regenerate", confidence=1.0,
                slots=dict(last_turn.get("slots") or {}), missing=[],
            )],
        }

    # 完全相同的重复问题在短时间窗口内直接复用，避免两次结果漂移。
    if _same_query(last_turn, text) and _last_turn_is_fresh(last_turn):
        prior_intent = _as_intent(last_turn.get("intent"))
        return {
            "intent": prior_intent.value,
            "intents": list(last_turn.get("intents") or [prior_intent.value]),
            "tasks": list(last_turn.get("tasks") or []),
            "confidence": 1.0,
            "slots": dict(last_turn.get("slots") or {}),
            "destination_source": last_turn.get("destination_source", ""),
            "missing": [],
            "pending_intent": "",
            "pending_missing": [],
            "follow_up": "reuse",
            "route_source": "reuse",
            "normalized_query": last_turn.get("normalized_query") or last_turn.get("query") or text,
            "trace": [trace_event(
                "classify_and_extract", _elapsed_ms(t0),
                detail="相同问题在 10 分钟内，直接复用上一轮答案",
                source="reuse", confidence=1.0,
                slots=dict(last_turn.get("slots") or {}), missing=[],
            )],
        }

    # 解释型追问直接复用上一轮结果：不再跑 Jev/LLM，也不重新调用实时工具。
    if _is_explain_followup(text, last_turn):
        prior_intent = _as_intent(last_turn.get("intent"))
        return {
            "intent": prior_intent.value,
            "confidence": 1.0,
            "slots": dict(last_turn.get("slots") or {}),
            "destination_source": last_turn.get("destination_source", ""),
            "missing": [],
            "pending_intent": "",
            "pending_missing": [],
            "follow_up": "explain",
            "route_source": "followup",
            "normalized_query": text,
            "trace": [trace_event(
                "classify_and_extract", _elapsed_ms(t0),
                detail="解释上一轮推荐依据（复用结果，不重跑工具）",
                source="followup", confidence=1.0,
                slots=dict(last_turn.get("slots") or {}), missing=[],
                note="follow_up=explain",
            )],
        }

    previous = dict(state.get("slots") or {})
    thread_destination = (previous.get("destination")
                          or previous.get("target_city") or "")
    stamp = state.get("date_updated_at") or ""
    expired = False
    if any(previous.get(k) for k in ("date", "start_date", "end_date")) and date_is_stale(stamp):
        for key in ("date", "start_date", "end_date"):
            previous.pop(key, None)
        stamp = ""
        expired = True
    history = _history_text(state)
    rule_result = _rule_result(text)
    profile = dict(state.get("profile") or {}) or load_profile(user_id)
    multi_tasks, multi_slots = [], {}
    normalized_query = text
    plan_used = False
    plan_note = ""

    # 复杂、口语化或碎片化表达优先交给 LLM 生成结构化任务计划。
    # LLM 只做“理解与改写”，最终意图/槽位仍由白名单清洗和必填槽位校验。
    if _looks_complex_query(text) and llm.available():
        try:
            memory_text = memory_hint(user_id, profile, text)
            plan_payload, plan_err = llm.chat_json(
                prompts.query_plan_messages(history, memory_text, text),
                LLMQueryPlanPayload,
                max_tokens=max(cfg.CLASSIFY_MAX_TOKENS, 700),
            )
            if plan_payload is not None:
                planned_tasks, planned_slots = _llm_query_plan_tasks(
                    plan_payload, text, previous, profile)
                if planned_tasks:
                    multi_tasks, multi_slots = planned_tasks, planned_slots
                    normalized_query = (plan_payload.normalized_query or text).strip()
                    plan_used = True
                    plan_note = "LLM 结构化计划 %.2f，拆出 %d 个任务" % (
                        float(plan_payload.confidence or 0.0), len(planned_tasks))
            else:
                plan_note = "LLM 计划失败，回退规则拆分：%s" % plan_err
        except Exception as exc:
            plan_note = "LLM 计划异常，回退规则拆分：%s" % llm.error_of(exc)
    if not multi_tasks:
        multi_tasks, multi_slots = _multi_intent_tasks(text, previous, profile)

    # JevK5：单意图时先做结构化路由；复合请求由规则拆成多个任务，
    # 不再让 Jev 只选一个意图而漏答后半句。
    jev_result = None
    jev_note = (plan_note if multi_tasks else "")
    if multi_tasks and not jev_note:
        jev_note = "检测到复合请求，拆分为 %d 个任务" % len(multi_tasks)
    if not multi_tasks and jev.available():
        try:
            jev_result = jev.decide(jev.compose_state(history, text))
        except Exception as exc:
            jev_note = "Jev 决策失败，回退主链路：%s" % str(exc)[:120]

    result, source, note = None, "rule", ""
    fast_path = False
    if multi_tasks:
        primary = _as_intent(multi_tasks[0]["intent"])
        result = {
            "intent": primary,
            "confidence": 0.96 if plan_used else 0.95,
            "slots": multi_slots,
        }
        source = "llm+plan" if plan_used else "rule+multi"
        fast_path = True
        note = "、".join(task["intent"] for task in multi_tasks)

    jev_intent = jev.confident_intent(jev_result) if jev_result else None
    trust = float((jev_result or {}).get("confidence") or 0.0)
    pending = state.get("pending_intent") or ""
    slot_only_reply = bool(
        pending and len(text) <= 24
        and (slot_lib.parse_date_range(text) or slot_lib.parse_date(text)
             or slot_lib.parse_days(text) or slot_lib.detect_city(text))
        and rule_result["intent"] is Intent.CHITCHAT
    )
    if slot_only_reply:
        jev_intent = _as_intent(pending)
        trust = 1.0

    # 快速路径：Jev 有足够把握，而且规则槽位已经满足必填项时，跳过主 LLM
    # 的第二次意图抽取。对“明天西安到北京怎么走”这类边界清楚的问题通常省 1~2 秒。
    if jev_intent is not None and trust >= cfg.JEV_TRUST_CONFIDENCE:
        candidate = slot_lib.merge_slots(
            previous, rule_result["slots"],
            new_city=rule_result["slots"].get("destination"),
        )
        if not missing_slots(jev_intent, candidate, text):
            result = {
                "intent": jev_intent,
                "confidence": trust,
                "slots": rule_result["slots"],
            }
            source = "jev+rule"
            fast_path = True
            note = "Jev 高置信路由 %.2f；规则槽位完整，跳过主 LLM" % trust

    if result is None:
        if llm.available():
            payload, err = llm.chat_json(prompts.classify_messages(history, text), LLMIntentPayload)
            if payload is not None:
                result, source = _llm_result(payload), "llm"
            else:
                note = "大模型识别失败，已降级为规则式：%s" % err
        else:
            note = "未配置对话模型密钥，使用规则式识别"
        if result is None:
            result = rule_result

    if not fast_path and jev_intent is not None:
        agree = result["intent"] is jev_intent
        # 高置信度、或与主 LLM 一致、或主链路只是规则式时，采纳 Jev；
        # 低置信度且与主 LLM 冲突时保留主 LLM，避免 0.55~0.75 的误路由。
        if trust >= cfg.JEV_TRUST_CONFIDENCE or agree or source == "rule":
            result["intent"] = jev_intent
            result["confidence"] = trust
            source = "jev"
            note = (note + "；" if note else "") + "Jev 路由 %.2f" % trust
        else:
            note = (note + "；" if note else "") + (
                "Jev %.2f 与主 LLM 不一致，保留主 LLM 路由" % trust)

    # 追问后的补充回答：沿用上一轮待补槽位的意图。
    # 只在“很短 + 明确像槽位”时生效，避免把随后的“你好”误当成答案。
    if slot_only_reply:
        result["intent"] = _as_intent(pending)
        note = (note + "；" if note else "") + "沿用上一轮待补槽位的意图"

    intent = result["intent"]
    incoming = result["slots"] or {}
    previous = slot_lib.scope_previous_slots(
        previous, text, incoming, intent,
        last_intent=str(last_turn.get("intent") or ""),
    )
    merged = slot_lib.merge_slots(previous, incoming, new_city=incoming.get("destination"))
    destination_reference = _has_destination_reference(text)
    text_city = (slot_lib.detect_city(text)
                 or (rule_result.get("slots") or {}).get("destination"))
    if destination_reference:
        # “那里/那边”本身不是本轮明确城市；LLM 补出的值只能视为上下文承接。
        explicit_city = text_city
    else:
        explicit_city = (incoming.get("destination") or incoming.get("target_city")
                         or text_city)
    destination_source = ""
    if merged.get("destination"):
        if explicit_city:
            destination_source = "current"
        elif thread_destination and merged.get("destination") == thread_destination:
            destination_source = "thread"
    # 规则式日期优先：用户说“明天/后天/10月20日”时，以本地解析结果覆盖模型输出
    date_range = slot_lib.parse_date_range(text)
    rule_date = slot_lib.parse_date(text)
    if date_range:
        for key in ("start_date", "end_date", "days"):
            merged[key] = date_range[key]
        merged["date"] = date_range["start_date"]
        stamp = now_iso()
    elif rule_date:
        if intent is Intent.WEATHER:
            for key in ("start_date", "end_date", "days"):
                merged.pop(key, None)
        merged["date"] = rule_date
        stamp = now_iso()
    elif incoming.get("date"):
        if (intent is Intent.WEATHER and not incoming.get("end_date")
                and not incoming.get("days")):
            for key in ("start_date", "end_date", "days"):
                merged.pop(key, None)
        stamp = now_iso()

    # 天气没给日期时，沿用上一轮行程的天数（先规划3天、再问天气的常见路径）
    if intent is Intent.WEATHER and not merged.get("date"):
        n = merged.get("days")
        if isinstance(n, int) and 1 <= n <= 14 and not merged.get("end_date"):
            today = _dt.date.today()
            merged["date"] = today.isoformat()
            merged["start_date"] = today.isoformat()
            merged["end_date"] = (today + _dt.timedelta(days=n - 1)).isoformat()
            stamp = now_iso()
            note = (note + "；" if note else "") + "按上一轮行程天数取天气"

    # —— 长期偏好与跨会话目的地：只补缺失值，不覆盖本轮明确表达 ——
    if not merged.get("preferences") and profile.get("preferences"):
        merged["preferences"] = list(profile["preferences"])
        note = (note + "；" if note else "") + "已套用长期偏好"
    province = slot_lib.detect_province(text)
    if province:
        merged["province"] = province

    ambiguous_destination_reference = bool(
        destination_reference and not explicit_city and not thread_destination)
    if ambiguous_destination_reference and destination_source not in ("thread", "current"):
        merged.pop("destination", None)
        destination_source = ""
    if (not merged.get("destination") and profile.get("destination")
            and not province and not ambiguous_destination_reference):
        merged["destination"] = profile["destination"]
        destination_source = "profile"
        note = (note + "；" if note else "") + "沿用跨会话目的地"
    elif ambiguous_destination_reference:
        note = (note + "；" if note else "") + (
            "“那里”缺少当前会话指代，未使用跨会话城市猜测")
    if not merged.get("start_city") and profile.get("start_city"):
        merged["start_city"] = profile["start_city"]

    updates = {k: incoming[k] for k in
               ("preferences", "budget", "people", "destination", "start_city")
               if incoming.get(k) not in (None, "", [], {})}
    if updates:
        profile = save_profile(user_id, updates)

    province_needs_city = bool(province and (
        not merged.get("destination") or merged.get("destination") == province))
    if province_needs_city:
        merged.pop("destination", None)
        note = (note + "；" if note else "") + (
            "%s是省级行政区，请补充具体城市" % province)

    if multi_tasks:
        for task in multi_tasks:
            task_slots = slot_lib.merge_slots(merged, task.get("slots") or {})
            task["slots"] = task_slots
            task["missing"] = missing_slots(_as_intent(task["intent"]), task_slots, task["query"])
        missing = list(dict.fromkeys(
            key for task in multi_tasks for key in (task.get("missing") or [])
        ))
    else:
        missing = missing_slots(intent, merged, text)
    if province_needs_city:
        missing = ["destination_city"]
    elif ambiguous_destination_reference and "destination" not in missing:
        missing.append("destination")
    pending_out = intent.value if missing else ""
    urgent = bool(jev_result.get("urgent")) if jev_result else False
    emotion = jev_result.get("emotion") if jev_result else None
    return {
        "intent": intent.value,
        "intents": [task["intent"] for task in multi_tasks] if multi_tasks else [intent.value],
        "tasks": multi_tasks,
        "normalized_query": normalized_query or text,
        "confidence": result["confidence"],
        "slots": merged,
        "destination_source": destination_source,
        "missing": missing,
        "pending_intent": pending_out,
        "pending_missing": missing,
        "date_updated_at": stamp,
        "profile": profile,
        "route_source": source,
        "follow_up": "",
        "urgent": urgent,
        "emotion": emotion,
        "trace": [trace_event("classify_and_extract", _elapsed_ms(t0),
                              detail="%s（%s）" % (intent.value, source),
                              source=source, confidence=result["confidence"],
                              slots=merged, missing=missing, note=note,
                              normalized_query=normalized_query or text,
                              date_cleared=expired,
                              jev_confidence=(jev_result or {}).get("confidence"),
                              jev_latency_ms=(jev_result or {}).get("latency_ms"),
                              jev_note=jev_note,
                              urgent=urgent, emotion=emotion)],
    }


def route(state) -> str:
    """第一跳：只做追问、闲聊、工具或解释分支。"""
    if state.get("follow_up") == "explain":
        return "explain"
    if state.get("follow_up") in ("reuse", "regenerate"):
        return state.get("follow_up")
    return route_target(_as_intent(state.get("intent")), state.get("missing") or [])


def ask_clarify(state):
    """缺槽追问：只问最少必需信息，绝不猜测。"""
    t0 = time.perf_counter()
    missing = state.get("missing") or []
    labels = [SLOT_LABEL.get(key, key) for key in missing]
    answer = prompts.CLARIFY_TEMPLATE % "、".join(labels)
    return {"answer": answer,
            "pending_intent": state.get("intent", ""),
            "pending_missing": missing,
            "trace": [trace_event("ask_clarify", _elapsed_ms(t0), detail="、".join(labels),
                                  missing=missing)]}


def chat(state):
    """闲聊兜底（INT-07）。"""
    t0 = time.perf_counter()
    text = state.get("query") or ""
    answer, note = "", ""
    user_id = (state.get("user_id") or "demo").strip() or "demo"
    profile = dict(state.get("profile") or {}) or load_profile(user_id)
    recalled = _memory_recall_answer(user_id, profile, text)
    if recalled:
        return {
            "answer": recalled,
            "no_evidence": False,
            "degraded": False,
            "trace": [trace_event("chat", _elapsed_ms(t0),
                                  detail="从跨会话对话记录回忆目的地")],
        }
    if llm.available():
        try:
            context = _history_text(state)
            remembered = memory_hint(user_id, profile, text)
            if remembered:
                context = (context + "\n\n" + remembered).strip()
            res = llm.chat(prompts.chitchat_messages(context, text),
                           temperature=cfg.LLM_TEMPERATURE, max_tokens=200)
            answer = (res.get("text") or "").strip()
        except Exception as exc:
            note = "闲聊模型调用失败：%s" % llm.error_of(exc)
    if not answer:
        answer = ("你好，我是旅游规划助手。你可以问我：某个城市有什么好玩的、帮我规划几天行程、"
                  "怎么去某地、住哪里、有什么好吃的，以及某天的天气。")
    return {"answer": answer, "trace": [trace_event("chat", _elapsed_ms(t0), detail=note or "ok")]}


def _explain_citation(last_turn: dict) -> str:
    """复用上一轮引用编号，解释时不再生成第二套来源。"""
    citations = last_turn.get("citations") or []
    preferred = next((c for c in citations if c.get("kind") == "tool"), None)
    chosen = preferred or (citations[0] if citations else None)
    index = (chosen or {}).get("index")
    return " [%s]" % index if index else ""


def _hotel_facts(last_turn: dict) -> list:
    """从上一轮工具结果提取酒店名，供“为什么”节点解释，不访问网络。"""
    names = []
    for result in last_turn.get("tool_results") or []:
        data = result.get("data") or {}
        for value in data.get("酒店") or []:
            text = str(value)
            name = text.split("（", 1)[0].strip()
            if name:
                names.append(name)
    return list(dict.fromkeys(names))


def explain(state):
    """解释上一轮推荐的筛选方式与局限，不重跑 RAG/工具，也不复读列表。"""
    t0 = time.perf_counter()
    last_turn = dict(state.get("last_turn") or {})
    prior_intent = _as_intent(last_turn.get("intent"))
    slots = dict(last_turn.get("slots") or {})
    mark = _explain_citation(last_turn)

    if prior_intent is Intent.HOTEL:
        city = slots.get("destination") or slots.get("target_city") or "上一轮城市"
        center = slots.get("area") or city
        hotels = _hotel_facts(last_turn)
        lead = ("这些酒店不是按评分、性价比或好评率筛出的“最佳酒店”，"
                "而是上一轮按高德住宿 POI 返回结果整理的候选名单。%s" % mark)
        if hotels:
            detail = ("上一轮没有收到明确的商圈、预算或住宿偏好，因此以“%s”为中心，"
                      "在约 8 公里范围内查找“住宿服务”类型，并按接口返回顺序列出最多 8 条。"
                      "结果集中在同一片区域，主要就是这个检索范围造成的。%s"
                      % (center, mark))
        else:
            detail = ("上一轮查询以“%s”为中心查找住宿服务，并按接口返回顺序列出结果；"
                      "它没有经过二次评分排序。%s" % (center, mark))
        limit = ("高德这次只返回名称、区域和地址，没有返回价格、评分、房型，"
                 "也没有返回走到景点或地铁站的时间，所以系统不能声称哪一家更划算、"
                 "更方便或更安静。")
        advice = ("如果你希望我真正比较，请补充预算、目标商圈或地铁站，"
                  "以及“靠近景点、安静、亲子、可取消”等偏好；下一轮才能按条件重新筛选。")
        answer = "\n".join((lead, detail, limit, advice))
    elif prior_intent is Intent.FOOD:
        answer = "\n".join((
            "上一轮不是凭单一榜单推荐，而是把两类证据合在一起：知识库回答“吃什么”，"
            "高德补充附近餐厅名称。%s" % mark,
            "高德工具未返回评分、人均价格和营业状态，所以餐厅出现的先后不等于口味排名；"
            "知识库中的菜品介绍也不能证明某家店一定做得最好。",
            "若要按口味、预算、距离或营业中筛选，需要再提供商圈和人均预算。",
        ))
    elif prior_intent is Intent.ITINERARY:
        answer = "\n".join((
            "上一轮是按目的地、天数和偏好组织行程，再用知识库补充景点信息、"
            "用实时工具补充天气/位置等数据。%s" % mark,
            "它不是穷举所有路线后的数学最优解；证据不足、天气限制或工具不可用时，"
            "系统只能给出保守安排。",
            "如果要比较两套方案，请告诉我每天想去的重点、预算和体力强度。",
        ))
    else:
        answer = (
            "上一轮回答只依据当时返回的知识库与工具证据，并按引用编号逐句绑定。%s "
            "系统没有读取隐藏的用户画像，也不会用常识补造价格、评分或营业时间；"
            "证据没覆盖的部分无法给出确定结论。" % mark
        )

    return {
        "answer": answer,
        "citations": list(last_turn.get("citations") or []),
        "plan": {"ok": True, "issues": [], "follow_up": "explain",
                 "based_on_trace_id": last_turn.get("trace_id", "")},
        "no_evidence": False,
        "degraded": False,
        "evidence_binding_rate": 1.0,
        "trace": [trace_event("explain", _elapsed_ms(t0),
                              detail="复用上一轮证据解释推荐依据，未重跑工具",
                              based_on_trace_id=last_turn.get("trace_id", ""))],
    }


def _replay_previous(state, mode: str):
    """复用上一轮最终答案与引用，不访问任何外部工具或 RAG。"""
    t0 = time.perf_counter()
    last_turn = dict(state.get("last_turn") or {})
    answer = last_turn.get("answer") or NO_EVIDENCE_ANSWER
    detail = "重新回答：复用上一轮答案" if mode == "regenerate" else "相同问题：复用上一轮答案"
    return {
        "answer": answer,
        "citations": list(last_turn.get("citations") or []),
        "plan": {"ok": True, "issues": [], "follow_up": mode,
                 "based_on_trace_id": last_turn.get("trace_id", "")},
        "no_evidence": False,
        "degraded": bool(last_turn.get("degraded")),
        "evidence_binding_rate": float(last_turn.get("evidence_binding_rate") or 0.0),
        "trace": [trace_event(
            mode, _elapsed_ms(t0), detail=detail + "，未重跑工具和 RAG",
            based_on_trace_id=last_turn.get("trace_id", ""),
        )],
    }


def reuse_previous(state):
    return _replay_previous(state, "reuse")


def regenerate_previous(state):
    return _replay_previous(state, "regenerate")


def retrieve(state):
    """工具之后的 RAG 补充检索；只追加证据，不覆盖 API 结果。"""
    t0 = time.perf_counter()
    slots = state.get("slots") or {}
    query = state.get("normalized_query") or state.get("query") or ""
    city = slots.get("destination") or slots.get("target_city")
    ev = retrieval.build_evidence(query, city=city, category=slots.get("category"),
                                  entity=slots.get("entity"))
    chunks = retrieval.to_chunks(ev)
    extra_results = []
    entity_trace = []
    intents = set(_intents_of(state))
    if city and intents.intersection((Intent.DESTINATION, Intent.ITINERARY)):
        seen = set()
        names = []
        for chunk in chunks:
            title = str(chunk.get("title") or "").strip()
            if (title and title != city and 2 <= len(title) <= 30
                    and title not in seen):
                seen.add(title)
                names.append(title)
        provider = registry.get_provider("poi")
        for name in names[:4]:
            result = provider.run(city=city, entity=name)
            extra_results.append(result)
        if extra_results:
            extra_evidence = registry.to_evidence(extra_results)
            chunks.extend(extra_evidence)
            entity_trace.append(trace_event(
                "retrieve_rag_entities", 0,
                detail="RAG实体反查高德：%s" % "、".join(names[:4]),
                entities=names[:4],
                ok=sum(result.status is ToolStatus.OK for result in extra_results)))
    detail = "RAG 补充命中 %d 条" % len(chunks)
    if ev.get("error"):
        detail += "；降级原因：%s" % str(ev["error"])[:80]
    return {
        "evidence": list(state.get("evidence") or []) + chunks,
        "citations": (list(state.get("citations") or []) + list(ev.get("citations") or [])
                      + registry.to_citations([
                          item for item in chunks if item.get("kind") == "tool"
                          and item.get("source_id") not in {
                              c.get("source_id") for c in (ev.get("citations") or [])}
                      ])),
        "tool_results": (list(state.get("tool_results") or [])
                         + [dump(item) for item in extra_results]),
        "evidence_meta": retrieval.meta_of(ev),
        "trace": [trace_event("retrieve", _elapsed_ms(t0),
                              detail=detail + "（API 结果保留且优先）",
                              hits=len(chunks), degraded=bool(ev.get("degraded")),
                              phase="rag_supplement",
                              tool_evidence_count=len(state.get("evidence") or []))]
                 + entity_trace,
    }


def _parse_location(value):
    try:
        lon, lat = str(value or "").split(",", 1)
        return float(lon), float(lat)
    except (TypeError, ValueError):
        return None


def _haversine_km(first, second) -> float:
    """经纬度直线距离（公里）；只用于“更近”排序，不代表行驶距离。"""
    import math
    lon1, lat1 = first
    lon2, lat2 = second
    radius = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _item_data(item) -> dict:
    if isinstance(item, dict):
        return dict(item.get("data") or {})
    return dict(getattr(item, "data", None) or {})


def _attraction_points(items: list) -> list:
    for item in items or []:
        provider = item.get("provider") if isinstance(item, dict) else getattr(item, "provider", "")
        if provider == "amap-poi":
            return list(_item_data(item).get("_points") or [])
    return []


def _annotate_hotel_distances(results: list, last_turn: dict) -> None:
    """为酒店结果生成到上一轮/本轮行程景点的直线距离矩阵与最近景点。"""
    attractions = _attraction_points(results)
    if not attractions:
        attractions = _attraction_points((last_turn or {}).get("tool_results") or [])
    if not attractions:
        return
    for item in results:
        provider = item.get("provider") if isinstance(item, dict) else getattr(item, "provider", "")
        if provider != "amap_poi_hotel_search":
            continue
        data = _item_data(item)
        hotels = list(data.get("_points") or [])
        if not hotels:
            continue
        matrix, nearest_rows = [], []
        for hotel in hotels:
            hotel_xy = _parse_location(hotel.get("location"))
            if not hotel_xy:
                continue
            distances = []
            for attraction in attractions:
                attraction_xy = _parse_location(attraction.get("location"))
                if not attraction_xy:
                    continue
                km = round(_haversine_km(hotel_xy, attraction_xy), 2)
                row = {"酒店": hotel.get("name", ""),
                       "景点": attraction.get("name", ""), "直线距离公里": km}
                matrix.append(row)
                distances.append(row)
            if distances:
                distances.sort(key=lambda row: row["直线距离公里"])
                nearest = distances[:3]
                nearest_rows.append("%s：%s" % (
                    hotel.get("name", ""),
                    "、".join("%s（直线约%.2f公里）" % (
                        row["景点"], row["直线距离公里"]) for row in nearest)))
        data["酒店距离"] = nearest_rows
        data["_distance_matrix"] = matrix
        data["距离说明"] = "按经纬度计算直线距离，未计道路、交通方式和通勤时间"
        if isinstance(item, dict):
            item["data"] = data
        else:
            item.data = data


def call_tools(state):
    """调用外部工具，并把成功的工具结果转成同构证据。"""
    t0 = time.perf_counter()
    intents = _intents_of(state)
    slots = state.get("slots") or {}
    results = registry.run_for_intents(intents, slots)
    _annotate_hotel_distances(results, state.get("last_turn") or {})
    tool_evidence = registry.to_evidence(results)
    notes = registry.unavailable_notes(results)
    summaries = []
    for item in results:
        if item.status is ToolStatus.OK:
            hotels = (item.data or {}).get("酒店")
            plans = (item.data or {}).get("方案")
            foods = (item.data or {}).get("餐饮")
            if hotels:
                summaries.append("%s（%d 家）" % (item.provider, len(hotels)))
            elif plans:
                summaries.append("%s（%d 个方案）" % (item.provider, len(plans)))
            elif foods:
                summaries.append("%s（%d 家）" % (item.provider, len(foods)))
            else:
                summaries.append(item.provider)
        else:
            summaries.append("%s（不可用）" % item.provider)
    return {
        "tool_results": [dump(item) for item in results],
        "evidence": list(state.get("evidence") or []) + tool_evidence,
        "citations": list(state.get("citations") or []) + registry.to_citations(tool_evidence),
        "trace": [trace_event("call_tools", _elapsed_ms(t0),
                              status="degraded" if notes else "ok",
                              detail="｜".join(notes) if notes
                                     else ("、".join(summaries) + " — 全部成功"),
                              providers=[item.provider for item in results],
                              unavailable=notes)],
    }


def after_tools(state) -> str:
    """工具始终先执行；再按「智能搜索」开关补充 RAG。"""
    intents = _intents_of(state)
    if (state.get("smart_search", True)
            and any(INTENT_NEEDS_RETRIEVAL.get(intent, False) for intent in intents)):
        return "retrieve"
    return "validate"


def validate_plan(state):
    """冲突检查（PRD 第 4.1 节 validate_plan）：只报可核实的冲突，不臆断。"""
    t0 = time.perf_counter()
    intent = _as_intent(state.get("intent"))
    intents = _intents_of(state)
    slots = state.get("slots") or {}
    evidence = state.get("evidence") or []
    tools = state.get("tool_results") or []
    ok_tools = [item for item in tools if item.get("status") == _INT_TOOL_OK]
    issues = []

    if Intent.ITINERARY in intents:
        days = slots.get("days")
        people = slots.get("people") or 1
        budget = slots.get("budget")
        if not days:
            issues.append("缺少天数，无法生成逐日行程")
        if budget and days:
            per_person_day = float(budget) / max(int(days), 1) / max(int(people), 1)
            if per_person_day < 100:
                issues.append("人均每日预算约 %.0f 元，明显偏低，建议放宽预算或缩短天数"
                              % per_person_day)
    ok_weather = any(
        item.get("status") == _INT_TOOL_OK
        and _is_weather_provider(item.get("provider"))
        for item in tools
    )
    if Intent.WEATHER in intents and not ok_weather:
        issues.append("天气属于实时数据，必须来自工具；本次获取失败，不能凭知识库或常识作答")
    if not evidence and not ok_tools:
        issues.append("本轮没有取到任何可用证据")

    plan = {"ok": not issues, "issues": issues,
            "evidence_count": len(evidence), "tool_ok": len(ok_tools),
            "intent": intent.value,
            "intents": [item.value for item in intents]}
    return {"plan": plan, "trace": [trace_event("validate_plan", _elapsed_ms(t0),
                                                status="ok" if not issues else "warn",
                                                detail="；".join(issues) or "无冲突",
                                                issues=issues)]}


def _evidence_for_prompt(state: dict, limit: int = 4) -> list:
    """统一工具优先：工具证据排在 RAG 前，并给 RAG 留至少 1 个补充位。"""
    evidence = state.get("evidence") or []
    tools = [item for item in evidence if item.get("kind") == "tool"]
    knowledge = [item for item in evidence if item.get("kind") != "tool"]
    if not tools:
        return knowledge[:limit]
    if not knowledge:
        return tools[:limit]

    # 最多 limit-1 条工具，至少留 1 条 RAG 作背景补充。
    tool_budget = min(len(tools), max(1, limit - 1))
    picked = list(tools[:tool_budget])
    remaining = limit - len(picked)
    picked.extend(knowledge[:remaining])

    # 如果知识条目不够，再用剩余工具补满；始终保持工具在前。
    remaining = limit - len(picked)
    if remaining > 0:
        picked.extend(tools[tool_budget:tool_budget + remaining])
    return picked[:limit]


def _evidence_block(evidence: list, limit: int = 3, per_chars: int = 900) -> str:
    blocks = []
    for item in (evidence or [])[:limit]:
        head = item.get("title") or ""
        body = (item.get("text") or "")[:per_chars]
        blocks.append("[%s] %s\n%s" % (item.get("source_id", ""), head, body))
    return "\n\n".join(blocks)


def _simple_extractive(evidence: list, max_points: int = 3) -> str:
    """最后的兜底：直接用证据原文分点输出（零依赖、零幻觉）。"""
    points = []
    for item in (evidence or [])[:max_points]:
        text = (item.get("text") or "").strip().replace("\n", "；")
        if not text:
            continue
        points.append("%s [%s]" % (text[:180], item.get("source_id", "")))
    return "\n".join(points) if points else NO_EVIDENCE_ANSWER


def _with_notes(answer: str, unavailable: list) -> str:
    """把「哪些能力不可用」如实告诉用户（这是系统状态说明，不是事实陈述）。"""
    if not unavailable:
        return answer
    lines = [answer, "", "说明（系统状态）："]
    for item in unavailable:
        lines.append("- %s：%s" % (item.get("provider"), item.get("message") or item.get("error_code")))
    return "\n".join(lines)


_HEX_ID_RE = re.compile(r"[\[【]\s*([0-9a-fA-F]{8,16})\s*[\]】]")


def _number_citations(text: str, citations: list) -> tuple:
    """把 [hex source_id] 改成 [1][2]...，并返回按首次出现排序的引用。

    编号只用于展示；证据校验仍用原始 source_id。
    """
    text = text or ""
    referenced = []
    for match in _HEX_ID_RE.finditer(text):
        sid = match.group(1).lower()
        if sid not in referenced:
            referenced.append(sid)
    by_id = {(c.get("source_id") or "").lower(): c for c in (citations or [])}
    used, mapping = [], {}
    for idx, sid in enumerate(referenced, start=1):
        cite = by_id.get(sid)
        if not cite:
            continue
        mapping[sid] = idx
        item = dict(cite)
        item["index"] = idx
        used.append(item)

    def repl(m):
        sid = m.group(1).lower()
        return "[%d]" % mapping[sid] if sid in mapping else m.group(0)

    return _HEX_ID_RE.sub(repl, text), used


def _quality_limitations(state: dict, used_citations: list) -> list:
    """程序级固定补充限制说明，避免模型因长度限制省略 PRD 要求的边界。"""
    lines = []

    def mark(provider: str) -> str:
        cite = next((c for c in used_citations or [] if c.get("provider") == provider), None)
        return " [%s]" % cite.get("index") if cite and cite.get("index") else ""

    tools = [item for item in (state.get("tool_results") or [])
             if item.get("status") == _INT_TOOL_OK]
    has_transit = has_poi = has_hotel = has_food = has_hotel_distance = False
    centers = []
    for item in tools:
        provider = str(item.get("provider") or "")
        data = item.get("data") or {}
        if provider == "amap-route":
            try:
                distance = float(data.get("距离(公里)") or 0)
            except (TypeError, ValueError):
                distance = 0
            if distance > 800:
                lines.append("跨城驾车距离较远，不建议作为首选；高铁/航班的实时班次和余票"
                             "请以 12306 或航空公司为准" + mark(provider) + "。")
        elif provider == "amap_transit_route":
            has_transit = True
        elif provider == "amap-poi":
            has_poi = True
        elif provider == "amap_poi_hotel_search":
            has_hotel = True
            has_hotel_distance = bool(data.get("酒店距离"))
        elif provider == "amap_poi_food_search":
            has_food = True
        center = data.get("检索中心") or data.get("区域")
        if provider in ("amap-poi", "amap_poi_hotel_search", "amap_poi_food_search"):
            if center and center not in centers:
                centers.append(center)
    if has_transit:
        lines.append("公交/地铁内容是高德组合方案，不代表 12306 实时车次或余票。")
    if centers and not (state.get("slots") or {}).get("area"):
        lines.append("景点、住宿和餐饮默认以“%s”为检索中心；如需靠近具体景点、地铁站或商圈，"
                     "请继续指定区域。" % "、".join(centers[:3]))
    if has_hotel:
        lines.append("酒店工具未返回房价、房态、评分和房型，不能据此判断性价比。")
        if has_hotel_distance:
            lines.append("酒店到景点使用经纬度直线距离排序，未计道路、交通方式和实际通勤时间。")
        else:
            lines.append("本轮没有取得酒店与行程景点的完整坐标，无法计算“谁离景点更近”。")
    if has_food:
        lines.append("餐饮工具未返回评分、营业时间和人均价格，餐厅出现顺序不代表口味排名。")
    return list(dict.fromkeys(lines))


def _hotel_distance_appendix(state: dict, used_citations: list) -> str:
    """把酒店最近景点固定写入答案，确保“离景点更近”不依赖模型自愿转述。"""
    rows = []
    hotel_provider = ""
    for item in state.get("tool_results") or []:
        if item.get("status") != _INT_TOOL_OK:
            continue
        provider = str(item.get("provider") or "")
        distances = (item.get("data") or {}).get("酒店距离") or []
        if distances:
            rows.extend(str(x) for x in distances[:15])
            hotel_provider = provider
            break
    if not rows:
        return ""
    cite = next((c for c in used_citations or []
                 if c.get("provider") == hotel_provider), None)
    mark = " [%s]" % cite.get("index") if cite and cite.get("index") else ""
    return ("\n\n酒店到行程景点距离（直线）：\n"
            + "\n".join("- " + row + mark for row in rows))


def _profile_preference_notice(state: dict) -> str:
    prefs = list((state.get("profile") or {}).get("preferences") or [])
    if not prefs:
        return ""
    return "已结合你之前的长期偏好：%s。\n\n" % "、".join(prefs[:6])


def _destination_source_notice(state: dict) -> str:
    source = state.get("destination_source") or ""
    city = (state.get("slots") or {}).get("destination")
    if not city:
        return ""
    if source == "thread" and _has_destination_reference(state.get("query") or ""):
        return ("你问的“那里”按当前会话最近提到的%s理解。"
                "如果你指的是其他城市，请直接告诉我城市名。\n\n" % city)
    if source == "profile":
        return ("当前问题没有指定城市，暂按跨会话最近关注的目的地%s回答。"
                "如需换城市，请直接说明。\n\n" % city)
    return ""


def _ensure_task_headings(text: str, state: dict) -> str:
    """按任务自动插入稳定小标题，避免模型输出格式漂移。"""
    tasks = state.get("tasks") or []
    if len(tasks) <= 1:
        return text
    lines = text.splitlines()
    specs = [
        (Intent.DESTINATION, "景点推荐", ("景点",), r"景点|^\s*[-*].+"),
        (Intent.TRANSPORT, "交通方案", ("交通",),
         r"驾车|公交|地铁|高铁|航班|公里"),
        (Intent.FOOD, "美食推荐", ("美食", "餐饮"),
         r"餐厅|美食|小吃|烤鸭"),
        (Intent.HOTEL, "住宿建议", ("住宿", "酒店"),
         r"酒店|住宿|宾馆|公寓"),
    ]
    task_intents = {task.get("intent") for task in tasks}
    for intent, title, header_words, marker in specs:
        if intent.value not in task_intents:
            continue
        if any(any(word in line for word in header_words)
               and (line.startswith("**") or line.startswith("#"))
               for line in lines):
            continue
        pattern = re.compile(marker)
        index = next((i for i, line in enumerate(lines)
                      if pattern.search(line) and not line.startswith("已结合")), None)
        if index is not None:
            lines.insert(index, "**%s**" % title)
    return "\n".join(lines)


def _binding_below_gate(rate: float) -> bool:
    return float(rate or 0.0) < 0.7


def _answer_token_budget(state: dict) -> int:
    """按任务复杂度计算输出预算，避免三天行程或四任务回答被截断。"""
    tasks = state.get("tasks") or []
    intents = _intents_of(state)
    if len(tasks) > 1:
        return max(cfg.LLM_MAX_TOKENS, 2200)
    if Intent.ITINERARY in intents:
        return max(cfg.LLM_MAX_TOKENS, 1400)
    if any(item in intents for item in (Intent.HOTEL, Intent.FOOD)):
        return max(cfg.LLM_MAX_TOKENS, 1200)
    return max(cfg.LLM_MAX_TOKENS, 512)


_TASK_TERMS = {
    Intent.DESTINATION: ("景点", "好玩", "游玩", "公园", "博物馆", "长城"),
    Intent.ITINERARY: ("第一天", "第二天", "行程", "逐日"),
    Intent.TRANSPORT: ("交通", "路线", "驾车", "公交", "地铁", "航班", "高铁", "公里"),
    Intent.HOTEL: ("酒店", "住宿", "宾馆", "公寓"),
    Intent.FOOD: ("美食", "餐厅", "小吃", "烤鸭", "菜"),
    Intent.WEATHER: ("天气", "气温", "降水", "℃"),
}
def _answer_completion_issues(state: dict, text: str) -> list:
    """检查句子截断、逐日结构和复合任务覆盖。"""
    issues = []
    content = (text or "").strip()
    if not content:
        return ["回答为空"]
    core = re.sub(r"(?:\s*\[\d+\])+\s*$", "", content).rstrip()
    if not re.search(r"[。！？.!?）)\]】]$", core):
        issues.append("回答结尾不完整")
    intents = _intents_of(state)
    if Intent.ITINERARY in intents:
        days = int((state.get("slots") or {}).get("days") or 0)
        labels = ["第一天", "第二天", "第三天", "第四天", "第五天"]
        need = labels[:min(max(days, 1), len(labels))]
        if days:
            for index, label in enumerate(need):
                if label not in content:
                    issues.append("缺少完整逐日结构")
                    break
                start = content.find(label)
                end = content.find(need[index + 1], start) if index + 1 < len(need) else len(content)
                segment = content[start:end if end > start else len(content)]
                if not any(period in segment for period in ("上午", "下午", "晚间")):
                    issues.append("%s缺少上午/下午/晚间时段" % label)
                ranges = []
                for match in re.finditer(
                        r"(\d{1,2}:\d{2})\s*[-—~至]\s*(\d{1,2}:\d{2})",
                        segment):
                    ranges.append((match.group(1), match.group(2)))
                ranges.sort()
                if any(ranges[i][0] < ranges[i - 1][1] for i in range(1, len(ranges))):
                    issues.append("%s存在时间重叠" % label)
    tasks = state.get("tasks") or []
    if len(tasks) > 1:
        for task in tasks:
            try:
                task_intent = Intent(task.get("intent"))
            except Exception:
                continue
            terms = _TASK_TERMS.get(task_intent, ())
            if terms and not any(term in content for term in terms):
                issues.append("遗漏任务 %s" % task_intent.value)
    return list(dict.fromkeys(issues))


def compose(state):
    """组织答案：逐句绑定 source_id；生成失败退化为抽取式，绝不编造。"""
    t0 = time.perf_counter()
    query = state.get("query") or ""
    normalized_query = state.get("normalized_query") or query
    evidence = state.get("evidence") or []
    citations = state.get("citations") or []
    intent = _as_intent(state.get("intent"))
    intents = _intents_of(state)
    unavailable = [item for item in (state.get("tool_results") or [])
                   if item.get("status") != _INT_TOOL_OK]
    ok_tools = [item for item in (state.get("tool_results") or [])
                if item.get("status") == _INT_TOOL_OK]
    ok_weather = any(_is_weather_provider(item.get("provider"))
                     for item in ok_tools)

    # RAG 是背景补充，不能顶替实时天气。工具失败时仍如实拒答，
    # 避免把知识库里的旧天气或常识写成今天的实况。
    if intents == [Intent.WEATHER] and not ok_weather:
        return {"answer": _with_notes(NO_EVIDENCE_ANSWER, unavailable),
                "no_evidence": True, "degraded": True, "evidence_binding_rate": 0.0,
                "trace": [trace_event("compose", _elapsed_ms(t0), status="degraded",
                                      detail="实时天气工具失败；已取得的 RAG 仅作背景，不用于伪装实况")]}

    if not evidence:
        return {"answer": _with_notes(NO_EVIDENCE_ANSWER, unavailable),
                "no_evidence": True, "degraded": True, "evidence_binding_rate": 0.0,
                "trace": [trace_event("compose", _elapsed_ms(t0), status="degraded",
                                      detail="无证据，拒答")]}

    text, note, hint = "", "", ""
    kept, dropped, unbound = [], [], []
    tasks = state.get("tasks") or []
    picked = _evidence_for_prompt(state, limit=8 if len(tasks) > 1 else 4)
    if llm.available():
        try:
            hint = profile_hint(state.get("profile") or {})
            remembered = memory_hint(
                (state.get("user_id") or "demo").strip() or "demo",
                state.get("profile") or {},
                query,
            )
            if remembered:
                hint = (hint + "\n" if hint else "") + remembered
            if normalized_query and normalized_query != query:
                hint = (hint + "\n" if hint else "") + (
                    "语言理解后的完整问题：%s。回答时仍要覆盖用户原话中的全部任务。" % normalized_query)
            hint = (hint + "\n" if hint else "") + (
                "证据按“工具/API 优先、RAG 补充”排序。实时事实以工具为准；"
                "RAG 只补充景点、菜品、文化或一般规则，不得替代实时数值。")
            for tool in ok_tools:
                provider = str(tool.get("provider") or "")
                data = tool.get("data") or {}
                if provider == "amap-route":
                    try:
                        distance = float(data.get("距离(公里)") or 0)
                    except (TypeError, ValueError):
                        distance = 0
                    if distance > 800:
                        hint = (hint + "\n" if hint else "") + (
                            "本轮驾车距离超过 800 公里，不得把驾车列为首选；"
                            "应提示用户优先比较铁路或航班，没有实时车次/余票时明确要求查 12306/航司。")
                if provider == "amap_transit_route":
                    hint = (hint + "\n" if hint else "") + (
                        "公交/地铁结果只是高德组合方案，不代表 12306 实时车次与余票，必须如实说明。")
                if provider in ("amap_poi_hotel_search", "amap_poi_food_search", "amap-poi"):
                    center = data.get("检索中心") or data.get("区域") or data.get("城市")
                    radius = data.get("检索半径")
                    if center:
                        hint = (hint + "\n" if hint else "") + (
                            "本轮 POI 检索中心是“%s”%s；答案要明确这是默认参考区域，"
                            "用户若要其他商圈/落脚点可继续指定。"
                            % (center, ("，半径约 " + str(radius)) if radius else ""))
                if provider == "amap_poi_hotel_search" and data.get("酒店距离"):
                    hint = (hint + "\n" if hint else "") + (
                        "回答住宿时要利用“酒店距离”，按到上一轮行程景点的最近直线距离说明便利性；"
                        "同时明确这是直线距离，不是驾车或公共交通耗时。")
            if len(tasks) > 1:
                sections = "、".join(
                    "%s（%s）" % (task.get("query"), task.get("intent"))
                    for task in tasks
                )
                hint = (hint + "\n" if hint else "") + (
                    "这是复合请求，必须逐项回答，不能只回答第一个问题。任务顺序：%s。"
                    "每个任务使用清晰小标题（如景点、交通、美食、住宿）；"
                    "不同任务的事实分别绑定各自证据，并在最后给出简短的“限制与待确认”。" % sections
                )
            if any(task.get("intent") == Intent.HOTEL.value for task in tasks):
                hint = (hint + "\n" if hint else "") + (
                    "住宿任务必须优先完整列出本轮高德酒店工具返回的名称、区域和地址；"
                    "RAG 中的酒店或价格只能作为后置背景，不能替代高德本轮结果。"
                )
            if any(task.get("intent") == Intent.FOOD.value for task in tasks):
                hint = (hint + "\n" if hint else "") + (
                    "美食任务必须先用本轮高德餐饮工具返回的餐厅名称和地址回答附近吃什么，"
                    "再用 RAG 补充菜品、小吃或饮食文化；不得跳过工具餐厅名单。"
                )
            if Intent.ITINERARY in intents:
                hint = (hint + "\n" if hint else "") + (
                    "行程必须使用固定骨架：## 第一天（主题），下分“上午/下午/晚间”，"
                    "每项写景点、建议时长和交通衔接；最后写费用小计。第二、三、四天同样结构。"
                    "证据不足的时段写“待确认”，不得把合理建议伪装成已核实事实。")
            if state.get("thinking"):
                hint = (hint + "\n" if hint else "") + (
                    "用户开启了深度思考：请给出更详细的分日安排、取舍理由与注意事项；"
                    "仍然必须逐句标注来源，不得超出证据。")
            if state.get("urgent") or (state.get("emotion") or 0) >= 1.2:
                hint = (hint + "\n" if hint else "") + (
                    "用户表达了紧迫或不满：先给结论，再给步骤，语气简洁克制。")
            if _as_intent(state.get("intent")) is Intent.HOTEL:
                hint = (hint + "\n" if hint else "") + (
                    "住宿查询：只列酒店名称、区域和地址，不输出价格、房型、电话；"
                    "酒店名单以高德工具为准。RAG 若有相关的区域环境、周边景点或交通背景，"
                    "可在名单后用 1-2 句补充并保留引用；不相关就不写。")
            if _as_intent(state.get("intent")) is Intent.FOOD:
                hint = (hint + "\n" if hint else "") + (
                    "美食查询：知识库用于菜品/小吃介绍，高德工具用于餐厅名称；"
                    "分开列，不要编造菜品、价格或营业时间。")
            res = llm.chat_stream(prompts.compose_messages(
                                      _evidence_block(picked, limit=len(picked), per_chars=700),
                                      query, extra_rule=hint),
                                  temperature=0.0,
                                  max_tokens=_answer_token_budget(state),
                                  on_token=_emit_token)
            text = res.get("text") or ""
        except Exception as exc:
            note = "生成失败：%s" % llm.error_of(exc)
    else:
        note = "未配置对话模型密钥，使用抽取式作答"

    if text:
        try:
            from app.rag.generation.answer import post_validate
            kept_text, dropped, unbound = post_validate(text, citations)
            kept = [line for line in kept_text.split("\n") if line.strip()]
        except Exception as exc:
            note = "%s；引用校验不可用：%s" % (note, exc)

    degraded = False
    if not kept:
        degraded = True
        try:
            from app.rag.generation.answer import extractive_answer
            fallback = extractive_answer({"query": query, "chunks": picked})
        except Exception:
            fallback = _simple_extractive(picked)
        kept = [line for line in fallback.split("\n") if line.strip()]

    completion_issues = _answer_completion_issues(state, "\n".join(kept))
    if completion_issues and llm.available():
        try:
            retry_rule = (hint + "\n" if hint else "") + (
                "上一稿存在以下完整性问题：%s。请从头重新完整作答，保留引用编号；"
                "行程必须明确写第一天、第二天、第三天；复合问题每个任务都要有标题和结果；"
                "不要重复上一稿的开头，不要在中途结束。"
                % "；".join(completion_issues))
            retry = llm.chat_stream(prompts.compose_messages(
                                        _evidence_block(picked, limit=len(picked), per_chars=700),
                                        query, extra_rule=retry_rule),
                                    temperature=0.0,
                                    max_tokens=_answer_token_budget(state) + 600,
                                    on_token=_emit_token)
            retry_text = retry.get("text") or ""
            if retry_text:
                from app.rag.generation.answer import post_validate
                retry_kept_text, retry_dropped, retry_unbound = post_validate(
                    retry_text, citations)
                retry_kept = [line for line in retry_kept_text.split("\n") if line.strip()]
                retry_issues = _answer_completion_issues(state, "\n".join(retry_kept))
                if retry_kept and len(retry_issues) < len(completion_issues):
                    kept = retry_kept
                    dropped, unbound = retry_dropped, retry_unbound
                    completion_issues = retry_issues
                    note = (note + "；" if note else "") + "完整性问题已触发一次重写"
        except Exception as exc:
            note = (note + "；" if note else "") + (
                "完整性重写失败：%s" % llm.error_of(exc))
    if completion_issues:
        degraded = True

    total = len(kept) + len(dropped) + len(unbound)
    rate = round(len(kept) / total, 4) if total else 0.0
    binding_issue = _binding_below_gate(rate)
    if binding_issue:
        degraded = True
    numbered, used = _number_citations("\n".join(kept), citations)
    numbered = _ensure_task_headings(numbered, state)
    hotel_distance_text = _hotel_distance_appendix(state, used)
    if hotel_distance_text and "酒店到行程景点距离" not in numbered:
        numbered += hotel_distance_text
    limitations = _quality_limitations(state, used)
    if binding_issue:
        limitations.append("证据绑定率为 %.0f%%，低于 70%% 的规划质量门槛。" % (rate * 100))
    if limitations and "限制与待确认" not in numbered:
        numbered += "\n\n限制与待确认：\n" + "\n".join("- " + line for line in limitations)
    if completion_issues:
        numbered += ("\n\n说明（系统状态）：受当前证据限制，本轮输出为分日建议而非完整逐日行程。"
                     "缺口：%s。可补充落脚区域、必去景点或交通偏好后继续细化。"
                     % "、".join(completion_issues))
    numbered = (_destination_source_notice(state)
                + _profile_preference_notice(state) + numbered)
    return {
        "answer": _with_notes(numbered, unavailable),
        "citations": used,
        "degraded": degraded or bool(dropped or unbound),
        "no_evidence": False,
        "evidence_binding_rate": rate,
        "trace": [trace_event("compose", _elapsed_ms(t0),
                              status="degraded" if (degraded or dropped or unbound) else "ok",
                              detail=note or "ok",
                              kept=len(kept), dropped=len(dropped), unbound=len(unbound),
                              binding_rate=rate, limitations=limitations)],
    }


def persist(state):
    """落盘审计轨迹（PRD 可追溯）。失败只告警，不影响本轮答案。"""
    t0 = time.perf_counter()
    previous_turn = dict(state.get("last_turn") or {})
    if state.get("follow_up") in ("explain", "reuse", "regenerate"):
        # 连续追问/重述时仍指向最初那轮推荐，不能把“为什么/重复”覆盖成新对象。
        last_turn = previous_turn
    else:
        last_turn = {
            "trace_id": state.get("trace_id", ""),
            "query": state.get("query", ""),
            "normalized_query": state.get("normalized_query", ""),
            "intent": state.get("intent", ""),
            "intents": list(state.get("intents") or []),
            "tasks": list(state.get("tasks") or []),
            "slots": dict(state.get("slots") or {}),
            "destination_source": state.get("destination_source", ""),
            "answer": state.get("answer", ""),
            "citations": list(state.get("citations") or []),
            "tool_results": list(state.get("tool_results") or []),
            "plan": dict(state.get("plan") or {}),
            "degraded": bool(state.get("degraded")),
            "evidence_binding_rate": float(state.get("evidence_binding_rate") or 0.0),
            "completed_at": now_iso(),
        }
    user_id = (state.get("user_id") or "demo").strip() or "demo"
    append_turn(user_id, {
        "created_at": now_iso(),
        "trace_id": state.get("trace_id", ""),
        "thread_id": state.get("thread_id", ""),
        "query": state.get("query", ""),
        "normalized_query": state.get("normalized_query", ""),
        "answer": state.get("answer", ""),
        "intent": state.get("intent", ""),
        "intents": list(state.get("intents") or []),
        "tasks": list(state.get("tasks") or []),
        "slots": dict(state.get("slots") or {}),
        "destination_source": state.get("destination_source", ""),
        "route_source": state.get("route_source", ""),
        "citations": list(state.get("citations") or []),
        "tool_results": list(state.get("tool_results") or []),
        "plan": dict(state.get("plan") or {}),
        "degraded": bool(state.get("degraded")),
        "no_evidence": bool(state.get("no_evidence")),
        "evidence_binding_rate": float(state.get("evidence_binding_rate") or 0.0),
    })
    note = "轨迹落盘已关闭"
    if cfg.TRACE_ENABLED:
        record = {
            "trace_id": state.get("trace_id", ""),
            "thread_id": state.get("thread_id", ""),
            "user_id": user_id,
            "query": state.get("query", ""),
            "normalized_query": state.get("normalized_query", ""),
            "intent": state.get("intent", ""),
            "slots": state.get("slots") or {},
            "destination_source": state.get("destination_source", ""),
            "missing": state.get("missing") or [],
            "answer": state.get("answer", ""),
            "citations": [c.get("source_id") for c in (state.get("citations") or [])],
            "evidence_binding_rate": state.get("evidence_binding_rate", 0.0),
            "degraded": bool(state.get("degraded")),
            "trace": state.get("trace") or [],
        }
        try:
            cfg.TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)
            with cfg.TRACE_LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            # 不要把本机绝对路径写进轨迹/界面：演示时既噪又暴露目录结构
            note = "轨迹已写入"
        except Exception as exc:
            note = "轨迹落盘失败（不影响本轮答案）：%s" % exc
    return {
        "last_turn": last_turn,
        "trace": [trace_event("persist", _elapsed_ms(t0), detail=note)],
    }
