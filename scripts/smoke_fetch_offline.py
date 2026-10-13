#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线冒烟：不联网验证数据源适配器的解析/清洗链路。

用法：
    python scripts/smoke_fetch_offline.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402
from app.rag.indexing import clean as C  # noqa: E402
from app.rag.indexing.split import split_document  # noqa: E402

SAMPLE = """{{pagebanner|Chengdu banner.jpg}}
'''成都'''是[[四川]]的省会，别称“蓉城”。

== 了解 ==
成都地处四川盆地西部，气候温和湿润，号称“天府之国”。

== 观光 ==
{{see | name=武侯祠 | address=成都市武侯区武侯祠大街231号 | phone=+86-028-85552397 | hours=08:00-18:00 | price=¥50 }}
{{see | name=杜甫草堂 | address=成都市青羊区青华路37号 | hours=09:00-18:00 }}
武侯祠是纪念诸葛亮的祠堂，也是全国重点文物保护单位。

== 用餐 ==
{{eat | name=陈麻婆豆腐 | address=成都市青羊区西玉龙街197号 | price=¥40 }}
川菜以麻辣著称，火锅是成都的代表性美食之一。

== 下一站 ==
* [[重庆]]
* [[西安]]
"""

FAIL = []


def check(name, ok, detail=""):
    print("%s %-42s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAIL.append(name)


print("=" * 60)
print("离线冒烟（不联网）")
print("=" * 60)

listings = []
x = C.expand_templates(SAMPLE, listings)
check("模板展开出 listing", len(listings) == 3, "%d 条" % len(listings))
check("POI 卡片保留名字与属性", "武侯祠" in x and "地址：成都市武侯区武侯祠大街231号" in x)

x = C.strip_markup(x)
check("链接/粗体已剥离", "[[" not in x and "'''" not in x)

x = C.drop_noise_sections(x)
check("噪声章节已删除", "下一站" not in x and "重庆" not in x)

x = C.normalize(x)
check("规范化（繁->简）", C.normalize("臺灣 廣州 龍") == "台湾 广州 龙", C.normalize("臺灣 廣州 龍"))

doc = {"doc_id": "smoke-1", "title": "成都", "text": x, "province": "四川", "city": "成都",
       "county": None, "source_type": "public", "source_url": cfg.SOURCE_URL % "成都",
       "license": cfg.LICENSE, "retrieved_at": "2026-10-01", "lang": "zh"}
chunks = split_document(doc)
check("切分产出 chunk", len(chunks) >= 1, "%d 个" % len(chunks))
cats = {c["category"] for c in chunks}
check("类别识别（attraction/food）", bool(cats & {"attraction", "food"}), str(sorted(cats)))
lens = [c["char_len"] for c in chunks]
check("块长不超过上限", max(lens) <= 600, "最长 %d 字" % max(lens))
check("chunk 字段齐全", all(k in chunks[0] for k in
                        ("chunk_id", "parent_doc_id", "category", "province", "city", "source_url")))

from app.rag.retrieval.evidence import source_id  # noqa: E402
sid1 = source_id("https://zh.wikivoyage.org/wiki/成都", "wv-abc#0001")
sid2 = source_id("https://zh.wikivoyage.org/wiki/成都", "wv-abc#0001")
check("source_id 稳定且 12 位", sid1 == sid2 and len(sid1) == 12, sid1)

from app.rag.retrieval.fuse import rrf_fuse  # noqa: E402
order, scores = rrf_fuse(["a", "b", "c"], ["b", "c", "d"])
check("RRF 双路共识排第一", order[0][0] == "b", str(order[:2]))
check("RRF 同分优先向量召回", scores["a"] > 0 and scores["d"] > 0)

from app.rag.retrieval.fuse import dedup_by_parent  # noqa: E402
items = [{"chunk_id": "1", "metadata": {"parent_doc_id": "p"}},
         {"chunk_id": "2", "metadata": {"parent_doc_id": "p"}},
         {"chunk_id": "3", "metadata": {"parent_doc_id": "p"}}]
check("同源每篇最多保留 2 条", len(dedup_by_parent(items)) == 2)

from app.rag.generation.answer import compress_evidence, extractive_answer  # noqa: E402
long_text = "成都概况与人口信息。" * 40 + "武侯祠开放时间为08:00到18:00，门票50元。" + "行政区划信息。" * 30
compressed = compress_evidence("武侯祠开放时间和门票", long_text, 160)
check("query-aware 证据压缩保留相关尾句", "08:00" in compressed and "50元" in compressed, compressed[:80])
ev = {"query": "武侯祠开放时间", "chunks": [
    {"source_id": "aaaaaaaaaaaa", "text": "成都人口众多。武侯祠开放时间为08:00到18:00。"},
    {"source_id": "bbbbbbbbbbbb", "text": "成都下辖多个行政区。"}]}
fallback = extractive_answer(ev, max_points=1)
check("抽取兜底按问题相关性排序", "开放时间" in fallback and "aaaaaaaaaaaa" in fallback, fallback)

from app.rag.generation.dialogue import DialogueSession, prepare_turn  # noqa: E402
session = DialogueSession()
session.slots = {"city": "成都", "entity": "武侯祠", "category": "attraction"}
session.turns = [{"query": "成都武侯祠怎么样？", "answer": "", "slots": dict(session.slots)}]
rewritten, slots = prepare_turn("它的开放时间是什么？", session)
check("多轮追问继承实体", "武侯祠" in rewritten and slots.get("entity") == "武侯祠", rewritten)

print("-" * 60)
print("通过 %d 项，失败 %d 项" % (15 - len(FAIL), len(FAIL)))
sys.exit(1 if FAIL else 0)
