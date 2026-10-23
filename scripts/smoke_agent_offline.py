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
             "JEV_APP_KEY"):
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
jev_mod.available, jev_mod.decide = _real_jev_available, _real_jev_decide

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

# 3) 行程规划：检索 + 工具 + 逐句绑定
thread = "t-itinerary"
s = run("2人预算3000元，规划3天西安历史文化游", thread)
check("识别为行程规划", s.get("intent") == "INT-02", s.get("intent"))
check("槽位抽取完整", (s.get("slots") or {}).get("days") == 3
      and (s.get("slots") or {}).get("people") == 2
      and (s.get("slots") or {}).get("budget") == 3000, str(s.get("slots")))
check("走了检索分支", "retrieve" in nodes_of(s), str(nodes_of(s)))
check("走了工具分支", "call_tools" in nodes_of(s), str(nodes_of(s)))
answer = s.get("answer") or ""
check("答案带 source_id", ("[%s]" % SID_A) in answer or ("[%s]" % SID_B) in answer, answer[:60])

fact_lines = [line for line in answer.split("\n")
              if line.strip() and not line.startswith("说明") and not line.startswith("- ")
              and not line.startswith("知识库中")]
bound = [line for line in fact_lines if re.search(r"\[[0-9a-fA-F]{8,16}\]", line)]
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


def with_provider(stub, text):
    real = tool_registry.get_provider
    tool_registry.get_provider = lambda name: stub
    try:
        return run(text)
    finally:
        tool_registry.get_provider = real


s = with_provider(_OkProvider(), "明天北京天气怎么样")
check("识别为天气查询", s.get("intent") == "INT-06", s.get("intent"))
check("天气不检索知识库", "retrieve" not in nodes_of(s), str(nodes_of(s)))
check("天气调用工具", "call_tools" in nodes_of(s), str(nodes_of(s)))
check("工具证据进入引用", any(c.get("kind") == "tool" for c in (s.get("citations") or [])),
      str(s.get("citations"))[:70])
check("答案绑定工具引用",
      re.search(r"\[[0-9a-fA-F]{8,16}\]", s.get("answer") or "") is not None,
      (s.get("answer") or "")[:60])

# 5b) 工具不可用时：明确说明，且绝不编造实时数据
s = with_provider(_DownProvider(), "明天北京天气怎么样")
check("工具不可用时明确说明", "说明（系统状态）" in (s.get("answer") or ""), (s.get("answer") or "")[:40])
check("工具不可用时拒答不编造", s.get("no_evidence") is True, str(s.get("no_evidence")))

# 6) 交通：有日期才放行，缺日期要追问
s = run("广州到长沙高铁有哪些")
check("交通缺日期则追问", "ask_clarify" in nodes_of(s), str(nodes_of(s)))
s = run("明天从广州到长沙的高铁")
check("交通带日期走工具", "call_tools" in nodes_of(s) and "retrieve" not in nodes_of(s),
      str(nodes_of(s)))

# 7) 美食：走知识库
s = run("长沙有什么必吃的美食")
check("识别为美食推荐", s.get("intent") == "INT-05", s.get("intent"))
check("美食走检索", "retrieve" in nodes_of(s), str(nodes_of(s)))

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
