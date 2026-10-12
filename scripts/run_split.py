#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 documents.jsonl 做语义切分，产出 chunks.jsonl 与报告。"""

from __future__ import annotations

import collections
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg
from app.rag.indexing.split import split_document

OUT = cfg.PROCESSED
SEEN = set()
chunks = []
docs_all = [json.loads(l) for l in (OUT / "documents.jsonl").open(encoding="utf-8")]
for extra in ("wikipedia_docs.jsonl",):
    p = OUT / extra
    if p.exists():
        add = [json.loads(l) for l in p.open(encoding="utf-8")]
        print("并入 %s：%d 篇" % (extra, len(add)))
        docs_all += add

# 港澳台不在 GeoNames CN.txt 中，用标题白名单补省籍
PROV_OVERRIDE = {"香港": "香港", "澳门": "澳门", "澳门特别行政区": "澳门", "台湾": "台湾",
                 "臺灣": "台湾", "台北": "台湾", "高雄": "台湾", "台中": "台湾", "台南": "台湾"}
# 明确以中国为对象的旅行主题页（无省籍但属中国旅行知识）
CHINA_TOPICS = {"中国", "中国东北", "中国东南", "丝绸之路", "318国道", "京杭大運河沿線",
                "京杭大运河沿线", "中国到新加坡的铁路旅行", "中华老字号", "中国国家公园"}


def prov_of(d):
    return d.get("province") or PROV_OVERRIDE.get(d["title"])


def is_china_doc(d):
    """标题/父级能落到中国行政区，或属于白名单的中国旅行主题页。"""
    return bool(prov_of(d)) or d["title"] in CHINA_TOPICS


for d in docs_all:
    d["province"] = prov_of(d)
docs = [d for d in docs_all if is_china_doc(d)]
excluded = [d for d in docs_all if not is_china_doc(d)]
with (OUT / "excluded_non_china.jsonl").open("w", encoding="utf-8") as fh:
    for d in excluded:
        fh.write(json.dumps({"title": d["title"], "char_len": d["char_len"]}, ensure_ascii=False) + "\n")
print("相关性过滤：保留 %d 篇，剔除 %d 篇（疑似非中国目的地/主题页）" % (len(docs), len(excluded)))
print("  剔除样例:", [d["title"] for d in excluded[:15]])

for doc in docs:
    for c in split_document(doc):
        if c["content_hash"] in SEEN:
            continue
        SEEN.add(c["content_hash"])
        chunks.append(c)

with (OUT / "chunks.jsonl").open("w", encoding="utf-8") as fh:
    for c in chunks:
        fh.write(json.dumps(c, ensure_ascii=False) + "\n")

lens = [c["char_len"] for c in chunks]
cats = collections.Counter(c["category"] for c in chunks)
provs = collections.Counter(c["province"] for c in chunks if c["province"])
cities = collections.Counter(c["city"] for c in chunks if c["city"])
scope_cities, _ = cfg.load_scope()
in_range = sum(1 for n in lens if 350 <= n <= 600)

print("文档 %d 篇 -> chunk %d 个" % (len(docs), len(chunks)))
print("长度: 最小 %d | 中位 %d | 均值 %d | 最大 %d | 350-600 占比 %.1f%%" % (
    min(lens), statistics.median(lens), sum(lens) / len(lens), max(lens), 100.0 * in_range / len(lens)))
print("总字数 %s" % format(sum(lens), ","))
print("类别:", dict(cats.most_common()))
print("省级 Top10:", provs.most_common(10))
print("10 城 chunk 数:", {c: cities.get(c, 0) for c in scope_cities})
print("未标注省份的 chunk: %d（%.1f%%）" % (len(chunks) - sum(provs.values()),
      100.0 * (len(chunks) - sum(provs.values())) / len(chunks)))

rep = ["# 切分报告", "",
       "生成时间：%s" % datetime.now().astimezone().isoformat(timespec="seconds"), "",
       "参数：目标 450 字，重叠 80 字，块上限 600 字，最小 200 字；按 H2/H3 分节，重叠对齐到句子。", "",
       "| 指标 | 数值 |", "|---|---|",
       "| 输入文档 | %d 篇 |" % len(docs),
       "| 产出 chunk | %d 个 |" % len(chunks),
       "| 总字数 | %s |" % format(sum(lens), ","),
       "| 长度 最小/中位/均值/最大 | %d / %d / %d / %d |" % (min(lens), statistics.median(lens),
                                                              sum(lens) / len(lens), max(lens)),
       "| 落在 350-600 字 | %.1f%% |" % (100.0 * in_range / len(lens)), "",
       "## 类别分布", "", "| category | chunk |", "|---|---:|"]
for k, v in cats.most_common():
    rep.append("| %s | %d |" % (k, v))
rep += ["", "## 省级分布 Top20", "", "| 省份 | chunk |", "|---|---:|"]
for k, v in provs.most_common(20):
    rep.append("| %s | %d |" % (k, v))
rep += ["", "## 10 城", "", "| 城市 | chunk |", "|---|---:|"]
for c in scope_cities:
    rep.append("| %s | %d |" % (c, cities.get(c, 0)))
(OUT / "split_report.md").write_text("\n".join(rep), encoding="utf-8")
print("\n输出：data/processed/chunks.jsonl、split_report.md")
