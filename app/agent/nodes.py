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
import time
import datetime as _dt

from app.agent import config as cfg
from app.agent import jev, llm, prompts, retrieval
from app.agent import slots as slot_lib
from app.agent.intents import SLOT_LABEL, missing_slots, route_target
from app.agent.state import trace_event
from app.agent.store import load_profile, profile_hint, save_profile
from app.models.schemas import Intent, LLMIntentPayload, ToolStatus, dump, now_iso
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
        "user_id": user_id,
        "profile": profile,
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
    previous = dict(state.get("slots") or {})
    stamp = state.get("date_updated_at") or ""
    expired = False
    if any(previous.get(k) for k in ("date", "start_date", "end_date")) and date_is_stale(stamp):
        for key in ("date", "start_date", "end_date"):
            previous.pop(key, None)
        stamp = ""
        expired = True
    history = _history_text(state)

    # JevK5：先用结构化决策做意图路由（只判断，不生成文本）
    jev_result = None
    jev_note = ""
    if jev.available():
        try:
            jev_result = jev.decide(jev.compose_state(history, text))
        except Exception as exc:
            jev_note = "Jev 决策失败，回退主链路：%s" % str(exc)[:120]

    result, source, note = None, "rule", ""
    if llm.available():
        payload, err = llm.chat_json(prompts.classify_messages(history, text), LLMIntentPayload)
        if payload is not None:
            result, source = _llm_result(payload), "llm"
        else:
            note = "大模型识别失败，已降级为规则式：%s" % err
    else:
        note = "未配置对话模型密钥，使用规则式识别"
    if result is None:
        result = _rule_result(text)

    jev_intent = jev.confident_intent(jev_result) if jev_result else None
    if jev_intent is not None:
        result["intent"] = jev_intent
        result["confidence"] = float(jev_result.get("confidence") or 0.0)
        source = "jev"
        note = (note + "；" if note else "") + "Jev 路由 %.2f" % result["confidence"]

    # 追问后的补充回答：沿用上一轮待补槽位的意图。
    # 只在“很短 + 明确像槽位”时生效，避免把随后的“你好”误当成答案。
    pending = state.get("pending_intent") or ""
    if pending and len(text) <= 24:
        has_slot_signal = bool(
            slot_lib.parse_date_range(text) or slot_lib.parse_date(text)
            or slot_lib.parse_days(text) or slot_lib.detect_city(text)
        )
        if has_slot_signal and slot_lib.rule_intent(text) is Intent.CHITCHAT:
            result["intent"] = _as_intent(pending)
            note = (note + "；" if note else "") + "沿用上一轮待补槽位的意图"

    intent = result["intent"]
    incoming = result["slots"] or {}
    merged = slot_lib.merge_slots(previous, incoming, new_city=incoming.get("destination"))
    # 规则式日期优先：用户说“明天/后天/10月20日”时，以本地解析结果覆盖模型输出
    date_range = slot_lib.parse_date_range(text)
    rule_date = slot_lib.parse_date(text)
    if date_range:
        for key in ("start_date", "end_date", "days"):
            merged[key] = date_range[key]
        merged["date"] = date_range["start_date"]
        stamp = now_iso()
    elif rule_date:
        merged["date"] = rule_date
        stamp = now_iso()
    elif incoming.get("date"):
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

    # —— 长期偏好：只在用户本轮没表达偏好时补位，绝不覆盖本轮明确表达 ——
    profile = dict(state.get("profile") or {}) or load_profile(user_id)
    if not merged.get("preferences") and profile.get("preferences"):
        merged["preferences"] = list(profile["preferences"])
        note = (note + "；" if note else "") + "已套用长期偏好"
    updates = {k: incoming[k] for k in ("preferences", "budget", "people")
               if incoming.get(k) not in (None, "", [], {})}
    if updates:
        profile = save_profile(user_id, updates)

    missing = missing_slots(intent, merged)
    pending_out = intent.value if missing else ""
    urgent = bool(jev_result.get("urgent")) if jev_result else False
    emotion = jev_result.get("emotion") if jev_result else None
    return {
        "intent": intent.value,
        "confidence": result["confidence"],
        "slots": merged,
        "missing": missing,
        "pending_intent": pending_out,
        "pending_missing": missing,
        "date_updated_at": stamp,
        "profile": profile,
        "route_source": source,
        "urgent": urgent,
        "emotion": emotion,
        "trace": [trace_event("classify_and_extract", _elapsed_ms(t0),
                              detail="%s（%s）" % (intent.value, source),
                              source=source, confidence=result["confidence"],
                              slots=merged, missing=missing, note=note,
                              date_cleared=expired,
                              jev_confidence=(jev_result or {}).get("confidence"),
                              jev_latency_ms=(jev_result or {}).get("latency_ms"),
                              jev_note=jev_note,
                              urgent=urgent, emotion=emotion)],
    }


def route(state) -> str:
    """条件边：返回 ask / chat / retrieve / tools。"""
    target = route_target(_as_intent(state.get("intent")), state.get("missing") or [])
    # 「智能搜索」关闭时跳过知识库，只保留工具类能力
    if target == "retrieve" and not state.get("smart_search", True):
        return "tools"
    return target


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
    if llm.available():
        try:
            res = llm.chat(prompts.chitchat_messages(_history_text(state), text),
                           temperature=cfg.LLM_TEMPERATURE, max_tokens=200)
            answer = (res.get("text") or "").strip()
        except Exception as exc:
            note = "闲聊模型调用失败：%s" % llm.error_of(exc)
    if not answer:
        answer = ("你好，我是旅游规划助手。你可以问我：某个城市有什么好玩的、帮我规划几天行程、"
                  "怎么去某地、住哪里、有什么好吃的，以及某天的天气。")
    return {"answer": answer, "trace": [trace_event("chat", _elapsed_ms(t0), detail=note or "ok")]}


def retrieve(state):
    """检索知识库（PRD 第 6 章）。RAG 不可用时降级为无证据，不崩。"""
    t0 = time.perf_counter()
    slots = state.get("slots") or {}
    query = state.get("query") or ""
    city = slots.get("destination") or slots.get("target_city")
    ev = retrieval.build_evidence(query, city=city, category=slots.get("category"),
                                  entity=slots.get("entity"))
    chunks = retrieval.to_chunks(ev)
    detail = "命中 %d 条" % len(chunks)
    if ev.get("error"):
        detail += "；降级原因：%s" % str(ev["error"])[:80]
    return {
        "evidence": chunks,
        "citations": list(ev.get("citations") or []),
        "evidence_meta": retrieval.meta_of(ev),
        "trace": [trace_event("retrieve", _elapsed_ms(t0), detail=detail,
                              hits=len(chunks), degraded=bool(ev.get("degraded")))],
    }


def call_tools(state):
    """调用外部工具，并把成功的工具结果转成同构证据。"""
    t0 = time.perf_counter()
    intent = _as_intent(state.get("intent"))
    slots = state.get("slots") or {}
    results = registry.run_for_intent(intent, slots)
    tool_evidence = registry.to_evidence(results)
    notes = registry.unavailable_notes(results)
    return {
        "tool_results": [dump(item) for item in results],
        "evidence": list(state.get("evidence") or []) + tool_evidence,
        "citations": list(state.get("citations") or []) + registry.to_citations(tool_evidence),
        "trace": [trace_event("call_tools", _elapsed_ms(t0),
                              status="degraded" if notes else "ok",
                              detail="｜".join(notes) if notes else "全部成功",
                              providers=[item.provider for item in results],
                              unavailable=notes)],
    }


def validate_plan(state):
    """冲突检查（PRD 第 4.1 节 validate_plan）：只报可核实的冲突，不臆断。"""
    t0 = time.perf_counter()
    intent = _as_intent(state.get("intent"))
    slots = state.get("slots") or {}
    evidence = state.get("evidence") or []
    tools = state.get("tool_results") or []
    ok_tools = [item for item in tools if item.get("status") == _INT_TOOL_OK]
    issues = []

    if intent is Intent.ITINERARY:
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
    if intent is Intent.WEATHER and not ok_tools:
        issues.append("天气属于实时数据，必须来自工具；本次获取失败，不能凭知识库或常识作答")
    if not evidence and not ok_tools:
        issues.append("本轮没有取到任何可用证据")

    plan = {"ok": not issues, "issues": issues,
            "evidence_count": len(evidence), "tool_ok": len(ok_tools),
            "intent": intent.value}
    return {"plan": plan, "trace": [trace_event("validate_plan", _elapsed_ms(t0),
                                                status="ok" if not issues else "warn",
                                                detail="；".join(issues) or "无冲突",
                                                issues=issues)]}


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


def compose(state):
    """组织答案：逐句绑定 source_id；生成失败退化为抽取式，绝不编造。"""
    t0 = time.perf_counter()
    query = state.get("query") or ""
    evidence = state.get("evidence") or []
    citations = state.get("citations") or []
    unavailable = [item for item in (state.get("tool_results") or [])
                   if item.get("status") != _INT_TOOL_OK]

    if not evidence:
        return {"answer": _with_notes(NO_EVIDENCE_ANSWER, unavailable),
                "no_evidence": True, "degraded": True, "evidence_binding_rate": 0.0,
                "trace": [trace_event("compose", _elapsed_ms(t0), status="degraded",
                                      detail="无证据，拒答")]}

    text, note = "", ""
    kept, dropped, unbound = [], [], []
    if llm.available():
        try:
            hint = profile_hint(state.get("profile") or {})
            if state.get("thinking"):
                hint = (hint + "\n" if hint else "") + (
                    "用户开启了深度思考：请给出更详细的分日安排、取舍理由与注意事项；"
                    "仍然必须逐句标注来源，不得超出证据。")
            if state.get("urgent") or (state.get("emotion") or 0) >= 1.2:
                hint = (hint + "\n" if hint else "") + (
                    "用户表达了紧迫或不满：先给结论，再给步骤，语气简洁克制。")
            res = llm.chat_stream(prompts.compose_messages(_evidence_block(evidence), query, extra_rule=hint),
                                  temperature=0.0, max_tokens=cfg.LLM_MAX_TOKENS,
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
            fallback = extractive_answer({"query": query, "chunks": evidence})
        except Exception:
            fallback = _simple_extractive(evidence)
        kept = [line for line in fallback.split("\n") if line.strip()]

    total = len(kept) + len(dropped) + len(unbound)
    rate = round(len(kept) / total, 4) if total else 0.0
    return {
        "answer": _with_notes("\n".join(kept), unavailable),
        "degraded": degraded or bool(dropped or unbound),
        "no_evidence": False,
        "evidence_binding_rate": rate,
        "trace": [trace_event("compose", _elapsed_ms(t0),
                              status="degraded" if (degraded or dropped or unbound) else "ok",
                              detail=note or "ok",
                              kept=len(kept), dropped=len(dropped), unbound=len(unbound),
                              binding_rate=rate)],
    }


def persist(state):
    """落盘审计轨迹（PRD 可追溯）。失败只告警，不影响本轮答案。"""
    t0 = time.perf_counter()
    note = "轨迹落盘已关闭"
    if cfg.TRACE_ENABLED:
        record = {
            "trace_id": state.get("trace_id", ""),
            "thread_id": state.get("thread_id", ""),
            "query": state.get("query", ""),
            "intent": state.get("intent", ""),
            "slots": state.get("slots") or {},
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
    return {"trace": [trace_event("persist", _elapsed_ms(t0), detail=note)]}
