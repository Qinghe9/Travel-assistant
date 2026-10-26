#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Agent 命令行入口（单轮 / 多轮）。

用法：
    python scripts/run_agent.py "帮我规划3天西安行程"
    python scripts/run_agent.py "长沙有什么好吃的" --json
    python scripts/run_agent.py "明天北京天气" --show-trace
    python scripts/run_agent.py --chat
      :reset            清空本轮会话（换 thread_id）
      exit              退出

不配置任何密钥也能跑：意图走规则识别，答案走抽取式兜底（都不会编造事实）。
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="旅游规划助手 · Agent")
ap.add_argument("query", nargs="?", default=None, help="用户问题；省略或加 --chat 进入多轮")
ap.add_argument("--chat", action="store_true", help="多轮对话模式")
ap.add_argument("--thread-id", default=None, help="会话 ID（多轮要复用同一个）")
ap.add_argument("--json", action="store_true", help="输出完整 JSON")
ap.add_argument("--show-trace", action="store_true", help="打印每个节点的耗时与状态")
ap.add_argument("--memory", choices=["sqlite", "memory"], default=None, help="记忆后端")
a = ap.parse_args()

from app.agent import config as cfg            # noqa: E402
from app.agent import llm as agent_llm         # noqa: E402
from app.agent.graph import build_graph, mermaid   # noqa: E402
from app.agent.memory import make_checkpointer, memory_report   # noqa: E402
from app.models.schemas import INTENT_LABEL, Intent   # noqa: E402

graph = build_graph(checkpointer=make_checkpointer(a.memory))


def ask(text: str, thread_id: str) -> dict:
    """跑一轮，返回最终状态。"""
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke({"messages": [{"role": "user", "content": text}],
                         "thread_id": thread_id}, config)


def report(state: dict) -> None:
    intent = state.get("intent") or ""
    try:
        label = INTENT_LABEL[Intent(intent)]
    except Exception:
        label = ""
    print("问题：%s" % state.get("query", ""))
    print("意图：%s %s（置信度 %.2f｜来源 %s） 槽位：%s" % (
        intent, label, float(state.get("confidence") or 0.0),
        state.get("route_source") or "-", state.get("slots") or {}))
    if state.get("urgent") or state.get("emotion") is not None:
        print("路由信号：紧急=%s 情绪=%s" % (bool(state.get("urgent")), state.get("emotion")))
    if state.get("missing"):
        print("缺失：%s" % "、".join(state["missing"]))
    print("-" * 60)
    print(state.get("answer", ""))
    print("-" * 60)
    citations = state.get("citations") or []
    print("引用 %d 条 | 证据绑定率 %.2f | 无证据=%s | 降级=%s" % (
        len(citations), float(state.get("evidence_binding_rate") or 0.0),
        bool(state.get("no_evidence")), bool(state.get("degraded"))))
    for item in citations[:5]:
        print("  [%s] %s" % (item.get("source_id"), item.get("title") or item.get("provider") or "-"))
    if a.show_trace:
        print("-- 轨迹 --")
        for event in state.get("trace") or []:
            print("  %-22s %5sms  %-8s %s" % (
                event.get("node"), event.get("latency_ms"), event.get("status"),
                (event.get("detail") or "")[:70]))


def loop() -> None:
    thread_id = a.thread_id or str(uuid.uuid4())
    print("Agent 多轮模式（thread_id=%s）。:reset 换会话，exit 退出。" % thread_id)
    print("记忆：%s" % memory_report())
    while True:
        try:
            line = input("\n你 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line.lower() in ("exit", "quit", "q"):
            return
        if line == ":reset":
            thread_id = str(uuid.uuid4())
            print("已开始新会话：%s" % thread_id)
            continue
        report(ask(line, thread_id))


if __name__ == "__main__":
    if a.chat or not a.query:
        if not sys.stdin.isatty():
            try:
                sys.stdin.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        loop()
        sys.exit(0)

    result = ask(a.query, a.thread_id or str(uuid.uuid4()))
    if a.json:
        print(json.dumps({k: v for k, v in result.items()}, ensure_ascii=False, indent=2, default=str))
    else:
        report(result)
