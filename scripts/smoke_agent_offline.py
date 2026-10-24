#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 离线冒烟：不联网、不需要任何密钥，验证图结构与引用绑定逻辑。

用法：
    python scripts/smoke_agent_offline.py

设计说明：脚本会把检索替换成一个**固定的假证据源**，目的是验证
"证据 -> 逐句绑定 -> 引用校验"这条链路，而不是验证检索质量。
真实检索质量由 evals/run_rag_eval.py 负责。
"""

from __future__ import annotations

import re
import sys
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 强制离线：必须在导入 app.* 之前清掉密钥，否则本机 .env 里的真实 Key
# 会让"离线自检"真的去调 API（既花钱又不可重复）。设置空值后，
# app.rag.config 的 _load_dotenv 用 setdefault，不会再把 .env 的值填回来。
for _key in ("DEEPSEEK_API_KEY", "LLM_API_KEY", "RAG_EMBED_API_KEY", "RAG_SF_API_KEY",
             "OPENTRIPMAP_API_KEY", "ORS_API_KEY", "TRAVELPORT_CREDENTIALS", "AMAP_API_KEY",
             "JEV_APP_KEY", "SHOWAPI_APP_KEY"):
    os.environ[_key] = ""
os.environ["RAG_EMBED_PROVIDER"] = "local"
os.environ["AGENT_TRACE_LOG"] = str(Path(tempfile.gettempdir()) / "agent_smoke_traces.jsonl")
os.environ["AGENT_PROFILE_DB"] = str(Path(tempfile.gettempdir()) / "agent_smoke_profile.db")
for _p in (Path(os.environ["AGENT_TRACE_LOG"]), Path(os.environ["AGENT_PROFILE_DB"])):
    try:
        _p.unlink()
    except FileNotFoundError:
        pass

from app.agent import nodes, retrieval  # noqa: E402
from app.agent.graph import build_graph  # noqa: E402
from app.agent import slots as slot_lib  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

FAIL = []
TOTAL = [0]
SID_A = "aaaaaaaaaaaa"
SID_B = "bbbbbbbbbbbb"


def check(name, ok, detail=""):
    TOTAL[0] += 1
    print("%s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


def _chunk(cid, sid, text, meta):
    return {"chunk_id": cid, "source_id": sid, "text": text, "score_fused": 0.5, "metadata": meta}


def fake_evidence(query, city=None, category=None, entity=None, top_k=None):
    """固定证据源：只为验证绑定逻辑，不代表真实检索效果。"""
    meta1 = {"doc_title": "西安", "city": "西安", "province": "陕西",
             "source_url": "https://example.org/xian", "source_type": "public",
             "updated_at": "2026-10-01"}
    meta2 = dict(meta1, doc_title="西安城墙")
    return {
        "query": query,
        "chunks": [
            _chunk("wx#0000", SID_A, "西安城墙是中国现存规模最大的古城墙，可以租自行车环城。", meta1),
            _chunk("wx#0001", SID_B, "兵马俑位于西安临潼区，建议提前预约门票。", meta2),
        ],
        "citations": [{"source_id": SID_A, "title": "西安", "url": meta1["source_url"]},
                      {"source_id": SID_B, "title": "西安城墙", "url": meta2["source_url"]}],
        "no_evidence": False, "degraded": False, "empty": False,
        "retrieval_latency_ms": 5, "max_fused_score": 0.05, "max_vector_sim": 0.71,
        "timings": {}, "index_version": "smoke",
    }


print("=" * 64)
print("Agent 离线冒烟（不联网、不需要密钥）")
print("=" * 64)

# 用假证据源替换真实检索，其余链路保持原样
retrieval.build_evidence = fake_evidence
nodes.retrieval.build_evidence = fake_evidence

graph = build_graph(checkpointer=InMemorySaver())


class _StubProvider:
    """默认把所有工具替换成离线桩，保证整个自检真正不联网。"""

    name = "stub-tool"

    def fetch(self, **kwargs):
        return None

    def run(self, **kwargs):
        from app.models.schemas import ToolResult, ToolStatus
        return ToolResult(provider=self.name, status=ToolStatus.OK,
                          data={"提示": "离线桩"}, message="ok")


from app.tools import registry as tool_registry  # noqa: E402
_base_get_provider = tool_registry.get_provider
tool_registry.get_provider = lambda name: _StubProvider()


def run(text, thread=None):
    """跑一轮。不指定 thread 就新建一个，避免不同用例互相污染上下文。"""
    thread = thread or ("smoke-" + uuid.uuid4().hex[:8])
    return graph.invoke({"messages": [{"role": "user", "content": text}], "thread_id": thread},
                        {"configurable": {"thread_id": thread}})


def nodes_of(state):
    return [event.get("node") for event in state.get("trace") or []]


# 0) 槽位抽取的确定性回归（城市识别不能因为缺 scope_cities.json 而失效）
check("城市识别可用", slot_lib.detect_city("杭州西湖怎么玩") == "杭州",
      str(slot_lib.detect_city("杭州西湖怎么玩")))
check("中文天数解析", slot_lib.parse_days("帮我规划三天西安行程") == 3,
      str(slot_lib.parse_days("帮我规划三天西安行程")))
check("相对日期解析", slot_lib.parse_date("明天北京天气") is not None, "")
check("不猜测缺失日期", slot_lib.parse_date("北京天气怎么样") is None, "")

# 0b) 日期过期策略
import datetime as _dt  # noqa: E402
old_stamp = (_dt.datetime.now() - _dt.timedelta(hours=25)).isoformat(timespec="seconds")
fresh_stamp = _dt.datetime.now().isoformat(timespec="seconds")
check("25 小时前的日期视为过期", nodes.date_is_stale(old_stamp) is True, old_stamp)
check("刚更新的日期不过期", nodes.date_is_stale(fresh_stamp) is False, "")
check("没有时间戳视为过期", nodes.date_is_stale("") is True, "")

# 0c) 过期日期必须在真实图里被清空
_base_input = {"messages": [{"role": "user", "content": "天气怎么样"}],
               "user_id": "u-date", "slots": {"destination": "西安", "date": "2026-10-24"}}
s_old = graph.invoke(dict(_base_input, thread_id="date-old", date_updated_at=old_stamp),
                     {"configurable": {"thread_id": "date-old"}})
check("过期日期被清空并追问", "date" in (s_old.get("missing") or []), str(s_old.get("missing")))
s_fresh = graph.invoke(dict(_base_input, thread_id="date-fresh", date_updated_at=fresh_stamp),
                       {"configurable": {"thread_id": "date-fresh"}})
check("新鲜日期保留", "date" not in (s_fresh.get("missing") or []), str(s_fresh.get("missing")))

# 0d) 日期区间 + 追问后的意图承接
_rng = slot_lib.parse_date_range("最近三天")
check("最近三天解析为区间", bool(_rng) and _rng.get("days") == 3, str(_rng))
_week = slot_lib.parse_date_range("最近一周")
check("最近一周解析为7天", bool(_week) and _week.get("days") == 7, str(_week))
_two = slot_lib.parse_date_range("两星期")
check("两星期解析为14天", bool(_two) and _two.get("days") == 14, str(_two))
_next = slot_lib.parse_date_range("下周")
check("下周从周一开始", bool(_next)
      and _dt.date.fromisoformat(_next["start_date"]).weekday() == 0
      and _next.get("days") == 7, str(_next))
check("天河区抽取", slot_lib.parse_area("广州天河区附近有什么住宿酒店", "广州") == "天河区",
      str(slot_lib.parse_area("广州天河区附近有什么住宿酒店", "广州")))
check("商圈抽取", slot_lib.parse_area("珠江新城附近酒店") == "珠江新城",
      str(slot_lib.parse_area("珠江新城附近酒店")))

_t = "weather-mem"
run("帮我规划3天西安行程", _t)
s_weather = run("天气怎么样？", _t)
check("天气继承上一轮3天", not (s_weather.get("missing") or []), str(s_weather.get("missing")))
check("天气不再重复追问", "ask_clarify" not in nodes_of(s_weather), str(nodes_of(s_weather)))

_p = "weather-pending"
run("西安天气怎么样", _p)
s_pending = run("最近三天", _p)
check("追问回答承接天气意图", s_pending.get("intent") == "INT-06", s_pending.get("intent"))
check("追问回答补全日期区间", not (s_pending.get("missing") or []), str(s_pending.get("missing")))
check("区间槽位写入", (s_pending.get("slots") or {}).get("days") == 3
      and bool((s_pending.get("slots") or {}).get("end_date")),
      str(s_pending.get("slots")))

# 0e) Jev 决策路由：覆盖意图、写入紧急度；失败自动回退
from app.agent import jev as jev_mod  # noqa: E402
_real_jev_available, _real_jev_decide = jev_mod.available, jev_mod.decide
check("Jev 紧急概率方向正确",
      jev_mod._urgent_from_noul(0.99) is True
      and jev_mod._urgent_from_noul(0.01) is False,
      "")
jev_mod.available = lambda: True
jev_mod.decide = lambda text, timeout=None: {
    "choice": "INT-06", "confidence": 0.97,
    "probabilities": {"INT-06": 0.97, "INT-07": 0.03},
    "urgent": True, "emotion": 1.6, "latency_ms": 30, "model": "stub",
}
_s_jev = run("随便聊聊")
check("Jev 决策覆盖意图", _s_jev.get("intent") == "INT-06", _s_jev.get("intent"))
check("Jev 路由来源记录", _s_jev.get("route_source") == "jev", _s_jev.get("route_source"))
check("Jev 紧急度与情绪入状态", _s_jev.get("urgent") is True
      and (_s_jev.get("emotion") or 0) >= 1.2,
      "urgent=%s emotion=%s" % (_s_jev.get("urgent"), _s_jev.get("emotion")))

jev_mod.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("jev down"))
_s_fallback = run("你好")
check("Jev 失败自动回退", _s_fallback.get("intent") == "INT-07"
      and _s_fallback.get("route_source") != "jev",
      "%s / %s" % (_s_fallback.get("intent"), _s_fallback.get("route_source")))

# 低置信度 Jev 与主 LLM 冲突时，保留主 LLM
from types import SimpleNamespace  # noqa: E402
from app.agent import llm as llm_mod  # noqa: E402
_real_llm_avail, _real_llm_json = llm_mod.available, llm_mod.chat_json
llm_mod.available = lambda: True
llm_mod.chat_json = lambda messages, schema, **kw: (
    SimpleNamespace(intent="INT-07", confidence=0.9, slots={}), "")
jev_mod.decide = lambda text, timeout=None: {
    "choice": "INT-06", "confidence": 0.59, "probabilities": {},
    "urgent": False, "emotion": 0.2, "latency_ms": 30, "model": "stub",
}
_s_conflict = run("你好")
check("Jev 低置信度不覆盖 LLM", _s_conflict.get("intent") == "INT-07",
      "%s / %s" % (_s_conflict.get("intent"), _s_conflict.get("confidence")))
llm_mod.available, llm_mod.chat_json = _real_llm_avail, _real_llm_json

# 高置信 Jev + 规则槽位完整：跳过第二次主 LLM 意图抽取
_real_llm_stream = llm_mod.chat_stream
_llm_json_calls = [0]
llm_mod.available = lambda: True


def _must_not_classify(*args, **kwargs):
    _llm_json_calls[0] += 1
    raise AssertionError("快速路径不应调用主 LLM 意图抽取")


llm_mod.chat_json = _must_not_classify
llm_mod.chat_stream = lambda *a, **k: {"text": ""}
jev_mod.available = lambda: True
jev_mod.decide = lambda text, timeout=None: {
    "choice": "INT-04", "confidence": 0.96, "probabilities": {},
    "urgent": False, "emotion": 0.1, "latency_ms": 20, "model": "stub",
}
_s_fast = run("广州天河区酒店")
check("Jev+规则快速路径生效", _s_fast.get("route_source") == "jev+rule",
      _s_fast.get("route_source"))
check("快速路径跳过主 LLM", _llm_json_calls[0] == 0, str(_llm_json_calls[0]))
llm_mod.chat_stream = _real_llm_stream
llm_mod.available, llm_mod.chat_json = _real_llm_avail, _real_llm_json

jev_mod.available, jev_mod.decide = _real_jev_available, _real_jev_decide

# 0f) 酒店改走高德；不再走 Travelport
from app.agent.intents import INTENT_NEEDS_RETRIEVAL, INTENT_TOOLS  # noqa: E402
from app.models.schemas import Intent as _Intent  # noqa: E402
check("INT-04 只挂 hotel 工具", INTENT_TOOLS[_Intent.HOTEL] == ("hotel",),
      str(INTENT_TOOLS[_Intent.HOTEL]))
tool_registry.get_provider = _base_get_provider
_hotel_provider = tool_registry.get_provider("hotel")
check("hotel 映射到 amap_poi_hotel_search",
      getattr(_hotel_provider, "name", "") == "amap_poi_hotel_search",
      getattr(_hotel_provider, "name", ""))
tool_registry.get_provider = lambda name: _StubProvider()

_s_hotel = run("广州天河区附近有什么住宿酒店")
check("酒店意图走工具", _s_hotel.get("intent") == "INT-04" and "call_tools" in nodes_of(_s_hotel),
      str(_s_hotel.get("intent")))
_hotel_nodes = nodes_of(_s_hotel)
check("酒店先工具后补充 RAG",
      "call_tools" in _hotel_nodes and "retrieve" in _hotel_nodes
      and _hotel_nodes.index("call_tools") < _hotel_nodes.index("retrieve"),
      str(_hotel_nodes))
check("酒店不再调用 travelport", "travelport" not in str(_s_hotel.get("tool_results")),
      str(_s_hotel.get("tool_results"))[:80])

# 0g) RAG 向量缓存跨线程（修复 SqliteSaver 之外的 SQLite 报错）
from app.rag.indexing.embed import EmbedCache  # noqa: E402
_cache_path = Path(tempfile.gettempdir()) / "embed_cache_thread_test.sqlite"
try:
    _cache_path.unlink()
except FileNotFoundError:
    pass
_cache = EmbedCache(_cache_path)
_worker_result = []


def _cache_worker():
    try:
        _cache.put_many("m", [("k", [0.1, 0.2])])
        out, _missing = _cache.get_many(["k"], "m")
        _worker_result.append(len(out.get("k") or []))
    except Exception:
        _worker_result.append(-1)


_thread = threading.Thread(target=_cache_worker)
_thread.start()
_thread.join()
check("EmbedCache 跨线程可用", _worker_result == [2], str(_worker_result))

# 0h) 公交换乘：出发地/到达地点，不强制日期
_places = slot_lib.parse_route_places("从天河区到广州塔怎么坐地铁")
check("公交换乘地点抽取", _places == ("天河区", "广州塔"), str(_places))
_bus_slots = slot_lib.rule_extract("从天河区到广州塔怎么坐地铁")
check("origin/destination_place 入槽位",
      _bus_slots.get("origin") == "天河区"
      and _bus_slots.get("destination_place") == "广州塔", str(_bus_slots))
from app.agent.intents import missing_slots  # noqa: E402
check("地铁查询不强制日期",
      missing_slots(_Intent.TRANSPORT, _bus_slots, "从天河区到广州塔怎么坐地铁") == [],
      str(missing_slots(_Intent.TRANSPORT, _bus_slots, "从天河区到广州塔怎么坐地铁")))
check("高铁查询仍要求日期",
      "date" in missing_slots(_Intent.TRANSPORT,
                              {"origin": "广州", "destination_place": "长沙"},
                              "广州到长沙高铁有哪些"),
      "")
tool_registry.get_provider = _base_get_provider
_transit_provider = tool_registry.get_provider("transit")
check("transit 映射到 amap_transit_route",
      getattr(_transit_provider, "name", "") == "amap_transit_route",
      getattr(_transit_provider, "name", ""))
tool_registry.get_provider = lambda name: _StubProvider()

_s_bus = run("从天河区到广州塔怎么坐地铁")
check("地铁意图走工具不追问", _s_bus.get("intent") == "INT-03"
      and "call_tools" in nodes_of(_s_bus)
      and "ask_clarify" not in nodes_of(_s_bus),
      str(nodes_of(_s_bus)))

# 0i) ShowAPI 免费接口：节假日 / 空气质量
from app.agent.intents import INTENT_TOOLS as _TOOLS  # noqa: E402
check("行程挂节假日与空气质量",
      "holiday" in _TOOLS[_Intent.ITINERARY] and "air_quality" in _TOOLS[_Intent.ITINERARY],
      str(_TOOLS[_Intent.ITINERARY]))
tool_registry.get_provider = _base_get_provider
_holiday_provider = tool_registry.get_provider("holiday")
_air_provider = tool_registry.get_provider("air_quality")
check("holiday 映射 showapi_holiday",
      getattr(_holiday_provider, "name", "") == "showapi_holiday",
      getattr(_holiday_provider, "name", ""))
check("air_quality 映射 showapi_air_quality",
      getattr(_air_provider, "name", "") == "showapi_air_quality",
      getattr(_air_provider, "name", ""))
tool_registry.get_provider = lambda name: _StubProvider()

# 0j) 证据优先级：工具证据在前，RAG 留作背景补充
_ev_kb = [{"kind": "knowledge", "source_id": "k%d" % i, "text": "知识"} for i in range(3)]
_ev_tool = [{"kind": "tool", "source_id": "t1", "text": "工具"}]
_ev_tool2 = [{"kind": "tool", "source_id": "t2", "text": "工具2"}]
check("所有旅行意图工具后补 RAG",
      all(INTENT_NEEDS_RETRIEVAL[intent] for intent in
          (_Intent.DESTINATION, _Intent.ITINERARY, _Intent.TRANSPORT,
           _Intent.HOTEL, _Intent.FOOD, _Intent.WEATHER))
      and not INTENT_NEEDS_RETRIEVAL[_Intent.CHITCHAT],
      str(INTENT_NEEDS_RETRIEVAL))
_hotel_pick = nodes._evidence_for_prompt({"intent": "INT-04", "evidence": _ev_kb + _ev_tool})
check("酒店工具证据排在 RAG 前",
      [e["source_id"] for e in _hotel_pick] == ["t1", "k0", "k1", "k2"],
      str([e["source_id"] for e in _hotel_pick]))
_weather_pick = nodes._evidence_for_prompt({"intent": "INT-06", "evidence": _ev_kb + _ev_tool})
check("天气工具证据排在 RAG 前",
      [e["source_id"] for e in _weather_pick] == ["t1", "k0", "k1", "k2"],
      str([e["source_id"] for e in _weather_pick]))
_it_pick = nodes._evidence_for_prompt({"intent": "INT-02", "evidence": _ev_kb + _ev_tool + _ev_tool2})
_it_ids = [e["source_id"] for e in _it_pick]
check("行程工具在前 RAG 在后", _it_ids == ["t1", "t2", "k0", "k1"], str(_it_ids))

# 0k) 美食：先高德餐厅，再 RAG 补充菜品
check("INT-05 挂 food 工具", _TOOLS[_Intent.FOOD] == ("food",), str(_TOOLS[_Intent.FOOD]))
tool_registry.get_provider = _base_get_provider
_food_provider = tool_registry.get_provider("food")
check("food 映射 amap_poi_food_search",
      getattr(_food_provider, "name", "") == "amap_poi_food_search",
      getattr(_food_provider, "name", ""))
tool_registry.get_provider = lambda name: _StubProvider()
_food_pick = nodes._evidence_for_prompt({"intent": "INT-05", "evidence": _ev_kb + _ev_tool})
_food_ids = [e["source_id"] for e in _food_pick]
check("美食工具在前 RAG 在后", _food_ids == ["t1", "k0", "k1", "k2"], str(_food_ids))

# 1) 闲聊
s = run("你好")
check("闲聊走 chat 分支", s.get("intent") == "INT-07", s.get("intent"))
check("闲聊有回答", bool((s.get("answer") or "").strip()), (s.get("answer") or "")[:20])
check("闲聊不触发检索", "retrieve" not in nodes_of(s), str(nodes_of(s)))

# 2) 缺槽追问
s = run("帮我规划旅行")
check("缺槽触发追问", "ask_clarify" in nodes_of(s), str(nodes_of(s)))
check("追问文案不含猜测", "请补充" in (s.get("answer") or ""), s.get("answer"))
check("缺槽不进检索", "retrieve" not in nodes_of(s), str(nodes_of(s)))

# 3) 行程规划：工具 + 补充检索 + 逐句绑定
thread = "t-itinerary"
s = run("2人预算3000元，规划3天西安历史文化游", thread)
check("识别为行程规划", s.get("intent") == "INT-02", s.get("intent"))
check("槽位抽取完整", (s.get("slots") or {}).get("days") == 3
      and (s.get("slots") or {}).get("people") == 2
      and (s.get("slots") or {}).get("budget") == 3000, str(s.get("slots")))
check("走了检索分支", "retrieve" in nodes_of(s), str(nodes_of(s)))
check("走了工具分支", "call_tools" in nodes_of(s), str(nodes_of(s)))
_it_nodes = nodes_of(s)
check("行程先工具后 RAG",
      _it_nodes.index("call_tools") < _it_nodes.index("retrieve"), str(_it_nodes))
answer = s.get("answer") or ""
check("答案带脚注编号", "[1]" in answer or "[2]" in answer, answer[:60])
check("正文不再出现 hex 编号", re.search(r"\[[0-9a-fA-F]{8,16}\]", answer) is None, answer[:60])

fact_lines = [line for line in answer.split("\n")
              if line.strip() and not line.startswith("说明") and not line.startswith("- ")
              and not line.startswith("知识库中")]
bound = [line for line in fact_lines if re.search(r"\[\d+\]", line)]
check("每个事实句都绑定引用", fact_lines and len(bound) == len(fact_lines),
      "%d/%d" % (len(bound), len(fact_lines)))
check("绑定率 > 0", float(s.get("evidence_binding_rate") or 0) > 0, str(s.get("evidence_binding_rate")))

# 4) 多轮：同 thread 继承槽位
s2 = run("第二天改成亲子活动", thread)
check("多轮继承城市与天数", (s2.get("slots") or {}).get("destination") == "西安"
      and (s2.get("slots") or {}).get("days") == 3, str(s2.get("slots")))

# 5) 天气属于实时数据：必须走工具，且不得走知识库
#    工具本身用确定性桩替换，避免让 CI 依赖网络状态（真实联网由手工验证）
from app.models.schemas import ToolResult, ToolStatus  # noqa: E402
from app.tools import registry as tool_registry  # noqa: E402


class _OkProvider:
    name = "stub-weather"

    def fetch(self, city=None, date=None):
        """只为对齐真实 Provider 的接口签名（参数映射会按它过滤）。"""
        return None

    def run(self, **kwargs):
        return ToolResult(provider=self.name, status=ToolStatus.OK,
                          data={"城市": kwargs.get("city") or "", "天气": "晴",
                                "最高温(℃)": 20, "数据来源": "stub"},
                          message="ok")


class _DownProvider:
    name = "stub-down"

    def fetch(self, city=None, date=None):
        return None

    def run(self, **kwargs):
        return ToolResult(provider=self.name, status=ToolStatus.UNAVAILABLE,
                          message="模拟服务不可用", error_code="stub_down")


def with_provider(stub, text, thread=None):
    real = tool_registry.get_provider
    tool_registry.get_provider = lambda name: stub
    try:
        return run(text, thread)
    finally:
        tool_registry.get_provider = real


class _HotelProvider:
    name = "amap_poi_hotel_search"

    def __init__(self):
        self.calls = 0

    def fetch(self, city=None, area=None, keyword=None):
        return None

    def run(self, **kwargs):
        self.calls += 1
        return ToolResult(provider=self.name, status=ToolStatus.OK,
                          data={"城市": kwargs.get("city") or "广州",
                                "区域": kwargs.get("area") or "天河区",
                                "酒店": ["测试酒店甲（天河区，体育西路）",
                                         "测试酒店乙（天河区，珠江新城）"],
                                "数据来源": "高德地图"},
                          message="获取成功（仅名称，无价格/房型）")


# 解释追问：复用上一轮酒店证据，不能再次调用工具或复读列表
_hotel_stub = _HotelProvider()
_thread_explain = "explain-hotel-" + uuid.uuid4().hex[:6]
_s_explain_base = with_provider(_hotel_stub, "广州天河区有什么住宿", _thread_explain)
_s_why = with_provider(_hotel_stub, "为什么选这些酒店？", _thread_explain)
check("解释追问被识别", _s_why.get("follow_up") == "explain", _s_why.get("follow_up"))
check("解释追问进入 explain 节点", "explain" in nodes_of(_s_why), str(nodes_of(_s_why)))
check("解释追问不重跑工具", not (_s_why.get("tool_results") or [])
      and _hotel_stub.calls == 1,
      "tool_results=%s calls=%s" % (len(_s_why.get("tool_results") or []),
                                     _hotel_stub.calls))
check("解释说明检索范围与局限",
      all(word in (_s_why.get("answer") or "") for word in ("8 公里", "评分", "预算")),
      (_s_why.get("answer") or "")[:100])
check("解释保留上一轮引用",
      bool(_s_why.get("citations")) and _s_why.get("evidence_binding_rate") == 1.0,
      str(_s_why.get("citations"))[:100])
check("解释不覆盖原始推荐对象",
      (_s_why.get("last_turn") or {}).get("query") == "广州天河区有什么住宿",
      str((_s_why.get("last_turn") or {}).get("query")))


# 工具并行：三个 120ms 的工具应在一个波次完成，而不是串成 360ms
class _SleepProvider:
    name = "sleep-tool"

    def fetch(self):
        return None

    def run(self, **kwargs):
        time.sleep(0.12)
        return ToolResult(provider=self.name, status=ToolStatus.OK, message="ok")


_real_intent_tools = tool_registry.INTENT_TOOLS
_real_get_provider_parallel = tool_registry.get_provider
tool_registry.INTENT_TOOLS = {_Intent.ITINERARY: ("a", "b", "c")}
tool_registry.get_provider = lambda name: _SleepProvider()
_t0_parallel = time.perf_counter()
_parallel_results = tool_registry.run_for_intent(_Intent.ITINERARY, {})
_parallel_elapsed = time.perf_counter() - _t0_parallel
tool_registry.INTENT_TOOLS = _real_intent_tools
tool_registry.get_provider = _real_get_provider_parallel
check("工具并行执行", len(_parallel_results) == 3 and _parallel_elapsed < 0.30,
      "%.3fs" % _parallel_elapsed)


# 高德同一地点的并发地理编码只应发出一次请求
from app.tools import amap as amap_mod  # noqa: E402
_real_amap_get, _real_amap_key = amap_mod._get, amap_mod.api_key
_geo_calls = [0]
_geo_count_lock = threading.Lock()


def _fake_geocode_get(path, params, timeout=4.0):
    with _geo_count_lock:
        _geo_calls[0] += 1
    time.sleep(0.10)
    return {"geocodes": [{"location": "108.9,34.3", "adcode": "610100",
                          "city": "西安市", "formatted_address": "西安市"}]}


amap_mod._get = _fake_geocode_get
amap_mod.api_key = lambda: "test"
amap_mod._GEO_CACHE.clear()
amap_mod._GEO_INFLIGHT.clear()
_geo_results, _geo_errors = [], []


def _geo_worker():
    try:
        _geo_results.append(amap_mod.geocode("西安")["location"])
    except Exception as exc:
        _geo_errors.append(exc)


_geo_threads = [threading.Thread(target=_geo_worker) for _ in range(3)]
for _geo_thread in _geo_threads:
    _geo_thread.start()
for _geo_thread in _geo_threads:
    _geo_thread.join()
amap_mod._get, amap_mod.api_key = _real_amap_get, _real_amap_key
amap_mod._GEO_CACHE.clear()
amap_mod._GEO_INFLIGHT.clear()
check("高德并发地理编码去重",
      _geo_calls[0] == 1 and _geo_results == ["108.9,34.3"] * 3 and not _geo_errors,
      "calls=%s results=%s errors=%s" % (_geo_calls[0], _geo_results, _geo_errors))


s = with_provider(_OkProvider(), "明天北京天气怎么样")
check("识别为天气查询", s.get("intent") == "INT-06", s.get("intent"))
check("天气调用工具", "call_tools" in nodes_of(s), str(nodes_of(s)))
_weather_nodes = nodes_of(s)
check("天气先工具后补充 RAG",
      "retrieve" in _weather_nodes
      and _weather_nodes.index("call_tools") < _weather_nodes.index("retrieve"),
      str(_weather_nodes))
check("工具证据进入引用", any(c.get("kind") == "tool" for c in (s.get("citations") or [])),
      str(s.get("citations"))[:70])
check("答案绑定工具引用",
      re.search(r"\[\d+\]", s.get("answer") or "") is not None,
      (s.get("answer") or "")[:60])

# 5b) 工具不可用时：明确说明，且绝不编造实时数据
s = with_provider(_DownProvider(), "明天北京天气怎么样")
check("工具不可用时明确说明", "说明（系统状态）" in (s.get("answer") or ""), (s.get("answer") or "")[:40])
check("工具不可用时拒答不编造", s.get("no_evidence") is True, str(s.get("no_evidence")))

# 6) 交通：有日期才放行，缺日期要追问
s = run("广州到长沙高铁有哪些")
check("交通缺日期则追问", "ask_clarify" in nodes_of(s), str(nodes_of(s)))
s = run("明天从广州到长沙的高铁")
_transport_nodes = nodes_of(s)
check("交通先工具后补充 RAG",
      "call_tools" in _transport_nodes and "retrieve" in _transport_nodes
      and _transport_nodes.index("call_tools") < _transport_nodes.index("retrieve"),
      str(_transport_nodes))

# 7) 美食：先高德工具，再 RAG 补充
s = run("长沙有什么必吃的美食")
check("识别为美食推荐", s.get("intent") == "INT-05", s.get("intent"))
_food_nodes = nodes_of(s)
check("美食先工具后补充 RAG",
      "call_tools" in _food_nodes and "retrieve" in _food_nodes
      and _food_nodes.index("call_tools") < _food_nodes.index("retrieve"),
      str(_food_nodes))

# 8) 无证据时不编造
def empty_evidence(query, city=None, category=None, entity=None, top_k=None):
    return {"query": query, "chunks": [], "citations": [], "no_evidence": True, "degraded": True,
            "empty": True, "retrieval_latency_ms": 1, "max_fused_score": 0.0, "max_vector_sim": 0.0,
            "timings": {}, "index_version": "smoke"}


retrieval.build_evidence = empty_evidence
nodes.retrieval.build_evidence = empty_evidence
s = with_provider(_DownProvider(), "成都必去的景点有哪些")
check("无证据时拒答不编造", s.get("no_evidence") is True
      and "无法作答" in (s.get("answer") or ""), (s.get("answer") or "")[:40])

print("-" * 64)
print("通过 %d 项，失败 %d 项" % (TOTAL[0] - len(FAIL), len(FAIL)))
if FAIL:
    print("失败项：", "、".join(FAIL))
sys.exit(1 if FAIL else 0)
