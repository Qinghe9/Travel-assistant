#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从维基百科 dump 分片补充城市条目（不走 API，不受限流影响）。

用法：
    python scripts/run_wikipedia.py [--limit 400] [--workers 8]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402
from app.rag.indexing import clean as C  # noqa: E402
from app.rag.indexing.sources import wikidump as wd  # noqa: E402
from app.rag.indexing.sources import wikipedia as wp  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--limit", type=int, default=400, help="最多补多少篇（0=不限）")
ap.add_argument("--workers", type=int, default=8)
ap.add_argument("--force", action="store_true", help="强制重新下载索引")
a = ap.parse_args()

OUT = cfg.PROCESSED
OUT.mkdir(parents=True, exist_ok=True)
DEST = OUT / "wikipedia_docs.jsonl"

cities, _ = cfg.load_scope()
print("目标城市 %d 个" % len(cities))

idx_path = cfg.RAW / "zhwiki" / "zhwiki-latest-pages-articles-multistream-index.txt.bz2"
print("步骤 1：准备 multistream 索引 ...")
wd.download_index(idx_path, force=a.force)
index = wd.load_index(idx_path)
print("  索引条目 %d 条" % len(index))

res1 = wd.resolve_titles(cities, index)
print("步骤 1：城市 %d 个，标题解析 %d 个" % (len(cities), len(res1)))

need = {}
for city, title in res1.items():
    need.setdefault(title, []).append(city)

if len(need) < a.limit or a.limit == 0:
    print("步骤 2：仍不足 %d 个市，补区县候选 %d 个" % (a.limit, len(need)))
    counties = set()
    for c in cities:
        for suf in ("区", "县"):
            counties.add(c + suf)
    res2 = wd.resolve_titles(sorted(counties), index)
    for cand, title in res2.items():
        need.setdefault(title, []).append(cand)
    print("  补充后候选 %d 个" % len(need))

if a.limit:
    need = dict(list(need.items())[: a.limit])

offset_map = {}
for title, names in need.items():
    offset_map.setdefault(index[title], []).append(title)
print("步骤 3：抓取 %d 个分片（含 %d 篇目标条目）..." % (len(offset_map), len(need)))

t0 = time.time()
pages = wd.fetch_streams(offset_map, workers=a.workers,
                         progress=lambda i, n: print("  分片 %d/%d" % (i, n), flush=True) if i % 50 == 0 else None)
print("  取回 %d 篇，耗时 %.0fs" % (len(pages), time.time() - t0))

docs = []
for title, raw in pages.items():
    x = C.expand_templates(raw)
    x = C.strip_markup(x)
    x = wp.filter_travel_sections(x)
    x = C.normalize(x)
    if len(x) < C.MIN_KEEP_CHARS:
        continue
    docs.append({
        "doc_id": "wp-%s" % hashlib.sha1(title.encode("utf-8")).hexdigest()[:12],
        "title": title,
        "parent_doc_id": None,
        "root_city": None,
        "source_url": "https://zh.wikipedia.org/wiki/%s" % urllib.parse.quote(title.replace(" ", "_")),
        "source_type": "public",
        "license": "CC BY-SA 4.0",
        "retrieved_at": "2026-10-01",
        "lang": "zh",
        "text": x,
        "char_len": len(x),
        "content_hash": hashlib.sha256(x.encode("utf-8")).hexdigest(),
        "listing_count": 0,
        "dup_paragraphs_dropped": 0,
        "province": None, "city": None, "county": None,
    })

with DEST.open("w", encoding="utf-8") as fh:
    for d in docs:
        fh.write(json.dumps(d, ensure_ascii=False) + "\n")
print("已写出 %s（%d 篇 / %s 字）" % (DEST, len(docs), format(sum(d["char_len"] for d in docs), ",")))
