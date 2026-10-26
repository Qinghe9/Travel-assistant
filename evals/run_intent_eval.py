#!/usr/bin/env python3
"""意图与槽位离线评测：混淆矩阵、宏平均和槽位 P/R/F1。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import llm, prompts
from app.agent.slots import rule_extract, rule_intent
from app.models.schemas import LLMIntentPayload


def online(query):
    payload, _ = llm.chat_json(
        prompts.classify_messages("", query), LLMIntentPayload)
    if payload is None:
        return rule_intent(query).value, rule_extract(query)
    return payload.intent, payload.slots or {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "online"), default="offline")
    args = parser.parse_args()
    rows = [json.loads(line) for line in
            (ROOT / "evals" / "intent_eval.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    confusion = defaultdict(Counter)
    per_intent_ok = Counter()
    per_intent_total = Counter()
    slot_counts = defaultdict(Counter)
    for row in rows:
        expected = row["expected_intent"]
        actual, slots = ((online(row["query"])) if args.mode == "online"
                         else (rule_intent(row["query"]).value, rule_extract(row["query"])))
        confusion[expected][actual] += 1
        per_intent_total[expected] += 1
        per_intent_ok[expected] += int(actual == expected)
        expected_slots = row.get("expected_slots") or {}
        for key in set(expected_slots) | set(slots):
            exp = expected_slots.get(key)
            got = slots.get(key)
            counts = slot_counts[key]
            counts["tp"] += int(exp not in (None, "", [], {}) and exp == got)
            counts["fp"] += int(exp in (None, "", [], {}) and got not in (None, "", [], {}))
            counts["fn"] += int(exp not in (None, "", [], {}) and exp != got)
    labels = sorted(per_intent_total)
    accuracy = {label: round(per_intent_ok[label] / per_intent_total[label], 4)
                for label in labels}
    macro = round(sum(accuracy.values()) / len(accuracy), 4) if accuracy else 0.0
    slot_metrics = {}
    for key, c in slot_counts.items():
        precision = c["tp"] / max(c["tp"] + c["fp"], 1)
        recall = c["tp"] / max(c["tp"] + c["fn"], 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-12)
        slot_metrics[key] = {"precision": round(precision, 4),
                             "recall": round(recall, 4), "f1": round(f1, 4)}
    result = {"mode": args.mode, "count": len(rows), "macro_accuracy": macro,
              "accuracy": accuracy,
              "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
              "slot_metrics": slot_metrics}
    out = ROOT / "evals" / "intent_metrics.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("mode=%s count=%d macro_accuracy=%.4f" % (args.mode, len(rows), macro))
    return 0 if macro >= 0.90 else 1


if __name__ == "__main__":
    raise SystemExit(main())
