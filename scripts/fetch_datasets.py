#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据源下载与许可登记：下载快照、计算 sha256、写入 data/manifest.csv。

用法：
    python scripts/fetch_datasets.py --list            # 查看数据源与本地状态
    python scripts/fetch_datasets.py --verify          # 校验 manifest 里每个文件的大小与哈希
    python scripts/fetch_datasets.py --only zhwikivoyage,geonames-cn
    python scripts/fetch_datasets.py                   # 下载全部 http 类数据源

说明：
    http 类数据源可直接用本脚本下载；ModelScope 类（ms-*）与 ChineseTravel 需要用
    ModelScope CLI/网页下载到 data/raw/<source>/<日期>/ 后，再用 --register 登记。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402

DATE = "20261011"
MANIFEST = cfg.DATA / "manifest.csv"
UA = {"User-Agent": "travel-assistant-rag/0.1 (course project)"}

SOURCES = {
    "zhwikivoyage": dict(
        url="https://dumps.wikimedia.org/zhwikivoyage/latest/zhwikivoyage-latest-pages-articles.xml.bz2",
        license="CC BY-SA 4.0", lang="zh", kind="http",
        file="zhwikivoyage-latest-pages-articles.xml.bz2"),
    "enwikivoyage": dict(
        url="https://dumps.wikimedia.org/enwikivoyage/latest/enwikivoyage-latest-pages-articles.xml.bz2",
        license="CC BY-SA 4.0", lang="en", kind="http",
        file="enwikivoyage-latest-pages-articles.xml.bz2"),
    "geonames-cn": dict(url="https://download.geonames.org/export/dump/CN.zip",
                        license="CC BY 4.0", lang="zh", kind="http", file="CN.zip"),
    "geonames-cities": dict(url="https://download.geonames.org/export/dump/cities15000.zip",
                            license="CC BY 4.0", lang="zh,en", kind="http", file="cities15000.zip"),
    "geonames-admin": dict(url="https://download.geonames.org/export/dump/admin1CodesASCII.txt",
                           license="CC BY 4.0", lang="en", kind="http", file="admin1CodesASCII.txt"),
    "zhwikipedia": dict(
        url="https://dumps.wikimedia.org/zhwiki/latest/zhwiki-latest-pages-articles-multistream-index.txt.bz2",
        license="CC BY-SA 4.0", lang="zh", kind="http", dir="wikipedia",
        file="zhwiki-latest-pages-articles-multistream-index.txt.bz2"),
    "chinatravel-sandbox-zh": dict(url="https://github.com/LongxingTan/ChineseTravel", kind="manual",
                                   license="见仓库 LICENSE", lang="zh", note="旅行对话语料，用仓库脚本导出 JSON"),
    "chinatravel-queries": dict(url="https://github.com/LongxingTan/ChineseTravel", kind="manual",
                                license="见仓库 LICENSE", lang="zh", note="查询集，从仓库数据导出"),
    "ms-address-ner": dict(url="https://modelscope.cn/datasets/modelscope/address_ner", kind="manual",
                           license="见数据集页面", lang="zh", note="ModelScope CLI: modelscope download --dataset ..."),
    "ms-geoglue-spec": dict(url="https://modelscope.cn/datasets/modelscope/GeoGLUE", kind="manual",
                            license="CC BY-NC 4.0", lang="zh", note="非商用，仅课程研究使用"),
    "ms-poi-national": dict(url="https://modelscope.cn/datasets/iic/POI", kind="manual",
                            license="见数据集页面（疑 ODbL）", lang="zh", note="全国 POI，需确认许可"),
    "ms-travel-dialogue": dict(url="https://modelscope.cn/datasets/modelscope/travel_dialogue", kind="manual",
                               license="见数据集页面", lang="zh", note="中文旅游多轮对话"),
    "ms-touind-qa": dict(url="https://modelscope.cn/datasets/modelscope/tourist_intent_qa", kind="manual",
                         license="见数据集页面", lang="zh", note="旅游意图问答"),
}


def sha256(path: Path, chunk=1 << 20):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def dest_of(name: str) -> Path:
    s = SOURCES[name]
    return cfg.RAW / s.get("dir", name) / DATE / s["file"]


def register(name: str, path: Path, rows: list):
    s = SOURCES[name]
    h = sha256(path)
    rows.append({
        "doc_id": "%s:%s" % (name, h[:12]),
        "source": name,
        "source_type": "public",
        "source_url": s["url"],
        "license": s["license"],
        "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "language": s.get("lang", "zh"),
        "content_hash": h,
        "bytes": path.stat().st_size,
        "snapshot_path": str(path.relative_to(ROOT)).replace("\\", "/"),
    })


def download(name: str):
    s = SOURCES[name]
    dest = dest_of(name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print("  已存在，跳过：%s（%.1f MB）" % (dest, dest.stat().st_size / 1e6))
        return dest
    print("  下载 %s -> %s" % (s["url"], dest))
    req = urllib.request.Request(s["url"], headers=UA)
    with urllib.request.urlopen(req, timeout=1800) as resp, dest.open("wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        got = 0
        while True:
            buf = resp.read(1 << 20)
            if not buf:
                break
            fh.write(buf)
            got += len(buf)
            if total and got % (20 << 20) < (1 << 20):
                print("    %.1f / %.1f MB" % (got / 1e6, total / 1e6), flush=True)
    print("  完成 %.1f MB" % (dest.stat().st_size / 1e6))
    return dest


def main():
    ap = argparse.ArgumentParser(description="RAG 数据源下载与许可登记")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--fix-manifest", action="store_true", help="按磁盘实际大小/哈希修正 manifest")
    ap.add_argument("--only", default="", help="逗号分隔的数据源名")
    ap.add_argument("--register", default="", help="登记已有文件：name=path1;path2")
    a = ap.parse_args()

    if a.list:
        print("%-24s %-9s %-14s %s" % ("数据源", "类型", "许可", "本地状态"))
        for n, s in SOURCES.items():
            p = dest_of(n) if "file" in s else cfg.RAW / s.get("dir", n)
            ok = "已下载" if (p.exists() if p.is_file() else p.exists()) else "缺失"
            print("%-24s %-9s %-14s %s" % (n, s["kind"], s["license"][:12], ok))
        return

    if a.verify or a.fix_manifest:
        rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
        missing, meta_bad, fixed = 0, 0, 0
        for r in rows:
            p = ROOT / r["snapshot_path"]
            if not p.exists():
                missing += 1
                if missing <= 5:
                    print("  缺失：%s" % r["snapshot_path"])
                continue
            size, h = p.stat().st_size, sha256(p)
            if int(r["bytes"] or 0) != size or r["content_hash"] != h:
                meta_bad += 1
                if a.fix_manifest:
                    r["bytes"], r["content_hash"] = size, h
                    fixed += 1
                elif meta_bad <= 5:
                    print("  元数据不符：%s（大小 %s/%d，哈希%s）" % (
                        r["snapshot_path"], r["bytes"], size,
                        "不同" if r["content_hash"] != h else "相同"))
        if a.fix_manifest and fixed:
            write_manifest(rows)
        print("校验 %d 行 | 文件缺失 %d | 元数据不符 %d%s" % (
            len(rows), missing, meta_bad, "（已按磁盘实际值修正 %d 行）" % fixed if a.fix_manifest else ""))
        sys.exit(1 if missing else 0)

    if a.register:
        name, _, paths = a.register.partition("=")
        rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8"))) if MANIFEST.exists() else []
        for p in [x for x in paths.split(";") if x]:
            register(name, Path(p), rows)
        write_manifest(rows)
        return

    only = [x.strip() for x in a.only.split(",") if x.strip()] or [n for n, s in SOURCES.items() if s["kind"] == "http"]
    rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8"))) if MANIFEST.exists() else []
    known = {(r["source"], r["snapshot_path"]) for r in rows}
    for n in only:
        if n not in SOURCES:
            print("  未知数据源：%s" % n); continue
        if SOURCES[n]["kind"] != "http":
            print("  %s 需手动下载（%s）" % (n, SOURCES[n].get("note", ""))); continue
        p = download(n)
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        if (n, rel) not in known:
            register(n, p, rows)
            print("  已登记 manifest：%s" % n)
    write_manifest(rows)


def write_manifest(rows):
    cols = ["doc_id", "source", "source_type", "source_url", "license", "retrieved_at",
            "language", "content_hash", "bytes", "snapshot_path"]
    with MANIFEST.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    print("manifest 已写入 %s（%d 行）" % (MANIFEST, len(rows)))


if __name__ == "__main__":
    main()
