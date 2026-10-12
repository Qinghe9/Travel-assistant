#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 data/scope_cities.json：从 GeoNames CN.zip 选出目标城市。

用法：
    python scripts/build_scope.py            # 默认：省会 + 直辖市 + 计划单列市（首批 10 城）
    python scripts/build_scope.py --all      # 全部地级市
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--all", action="store_true", help="输出全部地级市")
a = ap.parse_args()

CAPITALS = ["北京", "天津", "上海", "重庆", "广州", "深圳", "杭州", "南京", "苏州", "成都",
            "重庆", "武汉", "西安", "长沙", "郑州", "青岛", "沈阳", "哈尔滨", "昆明", "厦门"]
PROV_OF = {"北京": "北京", "天津": "天津", "上海": "上海", "重庆": "重庆"}

rows = []
with zipfile.ZipFile(cfg.GEONAMES_CN) as z, z.open("CN.txt") as fh:
    for line in io.TextIOWrapper(fh, encoding="utf-8"):
        c = line.rstrip("\n").split("\t")
        if len(c) >= 19:
            rows.append(c)
print("GeoNames CN.txt 行数 %d" % len(rows))

adm1 = {r[10]: r for r in rows if r[7] == "ADM1"}
cities = []
for r in rows:
    if r[7] != "ADM2":
        continue
    names = [n.strip() for n in ([r[1]] + (r[3] or "").split(",")) if n.strip()]
    zh = [n for n in names if all("\u4e00" <= ch <= "\u9fff" for ch in n)]
    if not zh:
        continue
    name = min(zh, key=len).rstrip("市") or min(zh, key=len)
    prov = adm1.get(r[10])
    prov_name = None
    if prov:
        pn = [n.strip() for n in ([prov[1]] + (prov[3] or "").split(",")) if n.strip()]
        pz = [n for n in pn if all("\u4e00" <= ch <= "\u9fff" for ch in n)]
        prov_name = min(pz, key=len).rstrip("省") if pz else None
    cities.append({"name_zh": name, "province": PROV_OF.get(name, prov_name),
                   "lat": float(r[4]), "lon": float(r[5]), "geonameid": int(r[0]),
                   "population": int(r[14] or 0)})

if not a.all:
    want = set(CAPITALS)
    cities = [c for c in cities if c["name_zh"] in want]

seen, out = set(), []
for c in sorted(cities, key=lambda x: -x["population"]):
    if c["name_zh"] in seen:
        continue
    seen.add(c["name_zh"])
    out.append(c)

payload = {"generated_at": "2026-10-01", "source": "GeoNames CN.zip (CC BY 4.0)",
           "count": len(out), "cities": out}
cfg.SCOPE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
print("已写出 %s：%d 个城市" % (cfg.SCOPE_FILE, len(out)))
print("  ", [c["name_zh"] for c in out][:20])
