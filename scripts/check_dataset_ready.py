#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据集就绪度检查：manifest 一致性 + 文件可读性 + 缺失项 + 流水线产物。

用法：
    python scripts/check_dataset_ready.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402

EXPECTED = [
    "zhwikivoyage", "enwikivoyage", "geonames-cn", "geonames-cities", "geonames-admin",
    "chinatravel-sandbox-zh", "chinatravel-queries", "ms-address-ner", "ms-geoglue-spec",
    "ms-poi-national", "ms-travel-dialogue", "ms-touind-qa", "zhwikipedia",
]
LABEL = {"zhwikivoyage": "中文维基导游 dump", "enwikivoyage": "英文维基导游 dump",
         "geonames-cn": "GeoNames 中国地名", "geonames-cities": "GeoNames 城市",
         "geonames-admin": "GeoNames 行政区划/别名", "chinatravel-sandbox-zh": "ChineseTravel 对话",
         "chinatravel-queries": "ChineseTravel 查询集", "ms-address-ner": "魔搭 中文地址 NER",
         "ms-geoglue-spec": "魔搭 GeoGLUE", "ms-poi-national": "魔搭 全国 POI",
         "ms-travel-dialogue": "魔搭 旅游对话", "ms-touind-qa": "魔搭 旅游意图问答",
         "zhwikipedia": "中文维基百科 dump 分片"}

print("== 1) data/manifest.csv ==")
if not (cfg.DATA / "manifest.csv").exists():
    print("  缺失 manifest.csv")
    sys.exit(1)
rows = list(csv.DictReader((cfg.DATA / "manifest.csv").open(encoding="utf-8")))
by = {}
for r in rows:
    by.setdefault(r["source"], []).append(r)
print("  条目 %d 行 | 数据源 %d 个" % (len(rows), len(by)))
missing_fields = [r for r in rows if not all(r.get(k) for k in ("source", "source_url", "license", "retrieved_at"))]
print("  字段不全的行：%d" % len(missing_fields))

print("\n== 2) 必需数据源 ==")
for s in EXPECTED:
    n = len(by.get(s, []))
    print("  %-24s %s %s" % (s, "OK " if n else "缺失", "%d 个文件" % n if n else ""))

print("\n== 3) 快照文件可读性 ==")
bad, total = 0, 0
for r in rows:
    p = ROOT / r["snapshot_path"] if r.get("snapshot_path") else None
    if not p or not p.exists():
        continue
    total += 1
    try:
        with p.open("rb") as fh:
            fh.read(4096)
    except Exception as e:  # noqa: BLE001
        bad += 1
        print("  不可读：%s（%s）" % (r["snapshot_path"], e))
print("  抽查 %d 个快照，不可读 %d 个" % (total, bad))

print("\n== 4) 残留与流水线产物 ==")
for name, path in [("documents.jsonl", cfg.PROCESSED / "documents.jsonl"),
                   ("wikipedia_docs.jsonl", cfg.PROCESSED / "wikipedia_docs.jsonl"),
                   ("chunks.jsonl", cfg.CHUNKS_FILE),
                   ("IndexManifest.json", cfg.MANIFEST_FILE),
                   ("bm25.pkl", cfg.BM25_FILE),
                   ("aliases.json", cfg.PROCESSED / "aliases.json"),
                   ("geo_index.json", cfg.PROCESSED / "geo_index.json")]:
    if path.exists():
        if path.suffix == ".jsonl":
            n = sum(1 for _ in path.open(encoding="utf-8"))
            print("  %-22s %s（%d 行）" % (name, "OK", n))
        else:
            print("  %-22s %s（%.1f MB）" % (name, "OK", path.stat().st_size / 1e6))
    else:
        print("  %-22s 缺失" % name)

print("\n== 5) 结论 ==")
need_missing = [s for s in EXPECTED if not by.get(s)]
print("  必需数据源缺失：%s" % ("、".join(need_missing) if need_missing else "无"))
print("  快照不可读：%d" % bad)
sys.exit(0 if not need_missing else 1)
