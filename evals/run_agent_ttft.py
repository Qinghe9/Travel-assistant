#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对真实 FastAPI SSE 接口运行 20 次 Agent 首 token 延迟评测。

用法（服务已启动在 8001）：
    python evals/run_agent_ttft.py
    python evals/run_agent_ttft.py --base-url http://127.0.0.1:8001 --runs 20
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def percentile(values: list, p: float) -> int:
    ordered = sorted(values)
    rank = max(math.ceil(p * len(ordered)) - 1, 0)
    return int(ordered[rank])


def run_once(client: httpx.Client, base_url: str, query: str, index: int) -> dict:
    thread_id = "ttft-%s-%s" % (index, uuid.uuid4().hex[:10])
    payload = {
        "message": query,
        "user_id": "ttft-eval",
        "thread_id": thread_id,
        "stream": True,
        "thinking": False,
        "smart_search": True,
    }
    started = time.perf_counter()
    client_ttft_ms = None
    server_ttft_ms = None
    final = {}
    with client.stream("POST", base_url + "/api/v1/chat", json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if not raw:
                continue
            event = json.loads(raw)
            if event.get("type") == "token" and client_ttft_ms is None:
                client_ttft_ms = int((time.perf_counter() - started) * 1000)
            elif event.get("type") == "final":
                final = event
                server_ttft_ms = event.get("ttft_ms")
    total_ms = int((time.perf_counter() - started) * 1000)
    ttft_source = "token_event"
    if client_ttft_ms is None:
        client_ttft_ms = total_ms
        ttft_source = "latency_fallback"
    return {
        "run": index,
        "thread_id": thread_id,
        "client_ttft_ms": client_ttft_ms,
        "ttft_source": ttft_source,
        "server_ttft_ms": server_ttft_ms,
        "total_ms": total_ms,
        "trace_id": final.get("trace_id", ""),
        "answer_chars": len(final.get("answer", "")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--query", default="北京有哪些必去的景点")
    parser.add_argument("--gate", type=float, default=2.0)
    parser.add_argument("--warmups", type=int, default=2)
    args = parser.parse_args()

    with httpx.Client(timeout=60.0) as client:
        health = client.get(args.base_url + "/health")
        health.raise_for_status()
        for index in range(args.warmups):
            run_once(client, args.base_url.rstrip("/"), args.query,
                     "warmup-%d" % (index + 1))
        rows = []
        for index in range(1, args.runs + 1):
            row = run_once(client, args.base_url.rstrip("/"), args.query, index)
            rows.append(row)
            print("[%02d/%02d] client TTFT=%dms server=%sms total=%dms" % (
                index, args.runs, row["client_ttft_ms"],
                row["server_ttft_ms"], row["total_ms"]))

    client_values = [row["client_ttft_ms"] for row in rows]
    total_values = [row["total_ms"] for row in rows]
    p50 = int(statistics.median(client_values))
    p95 = percentile(client_values, 0.95)
    server_values = [row["server_ttft_ms"] for row in rows
                     if isinstance(row.get("server_ttft_ms"), (int, float))]
    result = {
        "query": args.query,
        "runs": args.runs,
        "client_ttft_p50_ms": p50,
        "client_ttft_p95_ms": p95,
        "client_ttft_max_ms": max(client_values),
        "server_ttft_p50_ms": (int(statistics.median(server_values))
                               if server_values else None),
        "server_ttft_p95_ms": (percentile(server_values, 0.95)
                               if server_values else None),
        "warmups": args.warmups,
        "total_p50_ms": int(statistics.median(total_values)),
        "total_p95_ms": percentile(total_values, 0.95),
        "gate_ms": int(args.gate * 1000),
        "pass": p95 <= int(args.gate * 1000),
        "rows": rows,
    }
    output = ROOT / "evals" / "agent_ttft.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nTTFT P50=%dms P95=%dms max=%dms | 总耗时 P50=%dms P95=%dms" % (
        p50, p95, max(client_values), result["total_p50_ms"], result["total_p95_ms"]))
    print("%s 2 秒门禁 | 报告：%s" % ("PASS" if result["pass"] else "FAIL", output))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
