#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗 + 地名对齐 + 归属元数据。

用法：
    python scripts/run_clean.py --scope china [--skip-links]
    python scripts/run_clean.py --scope city  [--skip-links]

--scope china：全国语料（data/china_scope_titles.json）
--scope city ：10 城子树
"""

from __future__ import annotations

import concurrent.futures as cf
import csv
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg
from app.rag.indexing import clean as C
from app.rag.indexing.geo import GeoIndex
from app.rag.indexing.sources import wikivoyage as wv

SCOPE = "china"
SKIP_LINKS = False
for i, arg in enumerate(sys.argv[1:]):
    if arg.startswith("--scope="):
        SCOPE = arg.split("=", 1)[1]
    elif arg == "--scope":
        SCOPE = sys.argv[i + 2]
    elif arg == "--skip-links":
        SKIP_LINKS = True

OUT = cfg.PROCESSED
OUT.mkdir(parents=True, exist_ok=True)


def check_links(urls, limit=400, workers=12, timeout=5):
    urls = sorted(set(u for u in urls if u.startswith("http")))[:limit]

    def probe(u):
        for method in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(u, method=method, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; travel-assistant-rag/0.1)"})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return u, r.status, "ok", ""
            except urllib.error.HTTPError as e:
                if method == "HEAD" and e.code in (403, 405, 501):
                    continue
                return u, e.code, ("dead" if 400 <= e.code < 500 else "server_error"), str(e.reason)
            except Exception as e:  # noqa: BLE001
                return u, 0, "unreachable", type(e).__name__
        return u, 0, "unreachable", "HEAD/GET 均失败"

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(probe, urls))


def ancestors(tree, title, maxdepth=5):
    out, cur = [], title
    for _ in range(maxdepth):
        p = tree.get(cur)
        if not p or not p.parent:
            break
        out.append(p.parent)
        cur = p.parent
    return out


def main():
    t0 = time.time()
    cities, city_meta = cfg.load_scope()
    print("范围：%s" % ("全国（china_scope_titles.json）" if SCOPE == "china" else "、".join(cities)))

    pages = dict(wv.iter_pages(cfg.WIKIVOYAGE_DUMP))
    tree = wv.build_tree(pages)
    print("  ns0 条目 %d 篇" % len(pages))

    if SCOPE == "china":
        titles = cfg.load_china_titles()
        scope = {t: tree[t] for t in titles if t in tree}
        for t, p in scope.items():
            p.root_city = None
        print("  全国范围条目 %d 篇（清单 %d，缺失 %d）" % (len(scope), len(titles), len(titles) - len(scope)))
    else:
        scope = wv.select_scope(tree, cities)
        for t in pages:
            if t in scope:
                continue
            for c in cities:
                if t.startswith(c + "/"):
                    scope[t] = tree[t]; scope[t].root_city = c; scope[t].depth = 1
                    break
        print("  10 城范围条目 %d 篇" % len(scope))

    docs, redirects, dropped = [], {}, []
    all_listings, raw_chars = [], 0
    seen_para, seen_fp = set(), set()
    for title, page in sorted(scope.items()):
        raw = page.wikitext
        target = C.is_redirect(raw)
        if target:
            redirects[title] = target
            dropped.append({"title": title, "reason": "redirect", "target": target})
            continue
        raw_chars += len(raw)
        listings = []
        x = C.expand_templates(raw, listings)
        x = C.strip_markup(x)
        x = C.drop_noise_sections(x)
        x = C.normalize(x)
        paras, dup = C.dedup_paragraphs(x.split("\n"), 0.9, seen_para, seen_fp)
        text = "\n".join(paras)
        if len(text) < C.MIN_KEEP_CHARS:
            dropped.append({"title": title, "reason": "empty_after_clean",
                            "raw_len": len(raw), "clean_len": len(text)})
            continue
        all_listings.extend(listings)
        docs.append({
            "doc_id": "wv-%s" % hashlib.sha1(title.encode("utf-8")).hexdigest()[:12],
            "title": title,
            "parent_doc_id": page.parent,
            "root_city": page.root_city,
            "source_url": cfg.SOURCE_URL % urllib.parse.quote(title.replace(" ", "_")),
            "source_type": "public",
            "license": cfg.LICENSE,
            "retrieved_at": "2026-10-01",
            "lang": "zh",
            "text": text,
            "char_len": len(text),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "listing_count": len(listings),
            "dup_paragraphs_dropped": dup,
        })

    corpus = "\n".join(d["text"] for d in docs)
    print("\n清洗：%d 篇 | 原始 %s 字 | 清洗后 %s 字（保留 %.1f%%）" % (
        len(docs), format(raw_chars, ","), format(len(corpus), ","), 100.0 * len(corpus) / max(raw_chars, 1)))
    print("  丢弃 %d 篇（重定向 %d、空 %d）| listing 卡片 %d 条" % (
        len(dropped), len(redirects), sum(1 for d in dropped if d["reason"] == "empty_after_clean"), len(all_listings)))

    print("构建 GeoNames 索引（带缓存）...")
    cache = OUT / "geo_index.json"
    if cache.exists():
        cache.unlink()
    geo = GeoIndex.build(cfg.GEONAMES_CN, cache_path=cache, corpus_text=corpus)
    print("  地名记录 %d 条 | 别名 %d 条 | 规范名 %d" % (
        len(geo.name2rec), len(geo.alias2canon), len(geo.canon2alias)))

    for d in docs:
        prov, city, county = geo.resolve(d["title"], d["text"], ancestors(tree, d["title"]))
        d["province"], d["city"], d["county"] = prov, city, county

    with (OUT / "documents.jsonl").open("w", encoding="utf-8") as fh:
        for d in docs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with (OUT / "dropped.jsonl").open("w", encoding="utf-8") as fh:
        for d in dropped:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    (OUT / "aliases.json").write_text(json.dumps({
        "meta": {
            "source": "GeoNames CN.zip (CC BY 4.0) + 人工整理旅行别称",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "entries": len(geo.alias2canon),
        },
        "alias_to_canonical": geo.alias2canon,
        "canonical_to_aliases": {k: sorted(v) for k, v in geo.canon2alias.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    by_prov, by_city, no_meta = {}, {}, 0
    for d in docs:
        by_prov[d["province"]] = by_prov.get(d["province"], 0) + 1
        if d["city"]:
            by_city[d["city"]] = by_city.get(d["city"], 0) + 1
        if not d["province"]:
            no_meta += 1
    print("\n归属：%d 篇有省份 | %d 篇无省份 | %d 个地级市被标注" % (
        len(docs) - no_meta, no_meta, len(by_city)))
    print("  省级 Top12:", sorted(by_prov.items(), key=lambda x: -x[1])[:12])
    print("  10 城文档数:", {c: by_city.get(c, 0) for c in cities})

    rows = []
    if not SKIP_LINKS:
        urls = re.findall(r"https?://[^\s\|\[\]{}<>\"']+", "\n".join(p.wikitext for p in scope.values()))
        urls += [l.get("url", "") for l in all_listings]
        rows = check_links(urls)
        with (cfg.DATA / "dead_links.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh); w.writerow(["url", "http_status", "category", "detail", "checked_at"])
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            for u, code, kind, det in rows:
                w.writerow([u, code, kind, det, now])
    dead = sum(1 for r in rows if r[2] == "dead")
    unre = sum(1 for r in rows if r[2] == "unreachable")

    rep = ["# 清洗与地名对齐报告", "",
           "生成时间：%s" % datetime.now().astimezone().isoformat(timespec="seconds"),
           "范围：%s" % ("全国" if SCOPE == "china" else "、".join(cities)), "",
           "| 指标 | 数值 |", "|---|---|",
           "| 输入条目 | %d 篇 |" % len(scope),
           "| 丢弃（重定向/空） | %d 篇 |" % len(dropped),
           "| 产出文档 | %d 篇 |" % len(docs),
           "| 原始 wikitext | %s 字 |" % format(raw_chars, ","),
           "| 清洗后正文 | %s 字（保留 %.1f%%） |" % (format(len(corpus), ","), 100.0 * len(corpus) / max(raw_chars, 1)),
           "| listing 卡片 | %d 条 |" % len(all_listings),
           "| 段落去重丢弃 | %d 段 |" % sum(d["dup_paragraphs_dropped"] for d in docs),
           "| 地名别名词条 | %d |" % len(geo.alias2canon),
           "| 归属元数据（省/市） | %d 篇 / %d 个市 |" % (len(docs) - no_meta, len(by_city)),
           "| 外链：失效/不可达/正常 | %d / %d / %d |" % (dead, unre, len(rows) - dead - unre), "",
           "## 省级分布", "", "| 省份 | 文档 |", "|---|---:|"]
    for k, v in sorted(by_prov.items(), key=lambda x: -x[1]):
        if k:
            rep.append("| %s | %d |" % (k, v))
    rep += ["", "## 10 城文档数", "", "| 城市 | 文档 |", "|---|---:|"]
    for c in cities:
        rep.append("| %s | %d |" % (c, by_city.get(c, 0)))
    (OUT / "clean_report.md").write_text("\n".join(rep), encoding="utf-8")
    print("\n输出：data/processed/{documents.jsonl, dropped.jsonl, aliases.json, geo_index.json, clean_report.md}")
    print("耗时 %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
