#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 RAG 评测集：城市/县级/路线/卡片事实题 + 边界集。

用法：
    python scripts/build_rag_eval.py
    python scripts/build_rag_eval.py --cities 10 --per-city 6

注意：切分或重建索引之后必须重新运行本脚本，否则评测集里的 source_id 会与索引不同源，
评测脚本会直接报错拦下。
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402
from app.rag.retrieval.evidence import source_id  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--per-city", type=int, default=2, help="每个城市生成的模板题数量上限")
ap.add_argument("--llm-per-city", type=int, default=0, help="保留参数：预留 LLM 生成题量")
ap.add_argument("--seed", type=int, default=20261012)
a = ap.parse_args()
random.seed(a.seed)

EVALS = ROOT / "evals"
EVALS.mkdir(exist_ok=True)
chunks = [json.loads(l) for l in cfg.CHUNKS_FILE.open(encoding="utf-8")]
print("载入 chunk %d 个" % len(chunks))

# 卡片题用的 POI 名（来自 POI 卡片的第一段）
CARD_NAME = re.compile(r"^([^：（()。；，,、！？\n]{2,16})(?:[：:]|（)", re.M)
STOP = ("地址", "电话", "开放时间", "门票", "网址", "邮箱", "传真", "入住", "退房", "别名", "方位",
        "参见", "另见", "注释", "参考", "外部链接", "本条目", "位于", "该", "它", "这里", "如今",
        "目前", "当时", "其中", "此外", "因此", "但是", "同时", "由于")


def sid(c):
    return source_id(c.get("source_url", ""), c["chunk_id"])


by_city = collections.defaultdict(list)
by_county = collections.defaultdict(list)
for c in chunks:
    if c.get("city"):
        by_city[c["city"]].append(c)
    if c.get("county"):
        by_county[c["county"]].append(c)

items = []

# 1) 城市模板题（景点 / 美食）
city_names = [c for c, v in by_city.items() if len(v) >= 4]
random.shuffle(city_names)
for city in city_names:
    pool = by_city[city]
    for cat, q in (("attraction", "%s有哪些必去的景点"), ("food", "%s有什么特色美食")):
        rel = [c for c in pool if c["category"] == cat]
        if len(rel) < 2:
            continue
        items.append({"query": q % city, "city": city, "category": cat,
                      "type": "city_" + cat, "relevant_source_ids": sorted({sid(c) for c in rel})})
    if len(items) > a.per_city * len(city_names) * 2:
        break

# 2) 县级问题
county_names = [c for c, v in by_county.items() if len(v) >= 2]
random.shuffle(county_names)
for county in county_names[:60]:
    rel = by_county[county]
    items.append({"query": "%s有哪些值得去的地方" % county,
                  "city": rel[0].get("city"), "category": None, "type": "county",
                  "relevant_source_ids": sorted({sid(c) for c in rel})})

# 3) 卡片事实题（POI 在哪里）
card_items = []
for c in chunks:
    for m in CARD_NAME.finditer(c["text"]):
        name = m.group(1).strip()
        if len(name) < 2 or any(name.startswith(s) or name == s for s in STOP):
            continue
        if not c.get("city"):
            continue
        card_items.append((name, c))
        break
random.shuffle(card_items)
seen_fact_queries = set()
for name, c in card_items:
    fact_key = (c["city"], name)
    if fact_key in seen_fact_queries:
        continue
    seen_fact_queries.add(fact_key)
    # 同一实体可能因 overlap、来源合并或相邻卡片出现在多个 chunk；这些都是正确答案，
    # 不能只标注随机抽中的一个 chunk，否则会把精确召回误判为 false negative。
    rel = [x for x in by_city[c["city"]]
           if name in (x.get("text") or "")
           or name == (x.get("doc_title") or "")
           or name in (x.get("title_path") or "")]
    items.append({"query": "%s的%s 在哪里？" % (c["city"], name),
                  "city": c["city"], "category": None, "type": "fact_rule",
                   "entity": name,
                   "relevant_source_ids": sorted({sid(x) for x in (rel or [c])})})
    if len(seen_fact_queries) >= 60:
        break

# 4) 跨城路线题
transport = collections.defaultdict(list)
for c in chunks:
    if c.get("category") == "transport" and c.get("city"):
        transport[c["city"]].append(c)
t_cities = [c for c, v in transport.items() if v]
random.shuffle(t_cities)
for i in range(0, min(len(t_cities) - 1, 20), 2):
    a1, a2 = t_cities[i], t_cities[i + 1]
    rel = {sid(x) for x in transport[a1]} | {sid(x) for x in transport[a2]}
    items.append({"query": "从%s到%s怎么走" % (a1, a2), "city": None, "category": None,
                  "type": "route", "relevant_source_ids": sorted(rel)})

# 难负例：同省/邻省的其他城市（不进相关集，检验是否会误召）
with (EVALS / "rag_eval.jsonl").open("w", encoding="utf-8") as fh:
    for it in items:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")
print("rag_eval 草稿 %d 条" % len(items))
print("  类型分布:", dict(collections.Counter(it["type"] for it in items)))

# 边界集：知识库可得（冷门但域内） / 两者皆无（域外）
ko = [c for c in chunks if c["category"] == "attraction" and c.get("county") and c["char_len"] > 300]
random.shuffle(ko)
knowledge_only = [{"query": "%s有什么值得看的" % c["county"], "kind": "knowledge_only",
                   "relevant_source_ids": [sid(c)]} for c in ko[:10]]
neither = [{"query": q, "kind": "neither"} for q in [
    "量子纠缠退相干实验的低温恒温器型号有哪些",
    "如何用 Python 实现一个快速排序算法",
    "股票期权定价的 Black-Scholes 公式怎么推导",
    "糖尿病的一线用药方案是什么",
    "C 语言指针的内存对齐规则",
    "用 Rust 写一个异步 WebSocket 服务器",
    "国际空间站的轨道高度是多少",
    "卷积神经网络的感受野怎么计算",
    "如何用 SQL 优化百万行表的查询",
    "古希腊哲学的三大代表人物是谁",
]]
with (EVALS / "boundary_eval.jsonl").open("w", encoding="utf-8") as fh:
    for it in knowledge_only + neither:
        fh.write(json.dumps(it, ensure_ascii=False) + "\n")
print("已写出 %s（%d 条）" % (EVALS / "rag_eval.jsonl", len(items)))
print("已写出 %s（%d 条）" % (EVALS / "boundary_eval.jsonl", len(knowledge_only) + len(neither)))
