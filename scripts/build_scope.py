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
ap.add_argument("--level", choices=("city", "county", "both"), default="",
                help="生成地级市、县级表，或两者都生成")
a = ap.parse_args()
levels = {"city", "county"} if a.level == "both" else ({a.level} if a.level else set())
if a.all or not a.level:
    levels.add("city")
levels.discard("")

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
adm2 = {r[11]: r for r in rows if r[7] == "ADM2"}


def chinese_names(row):
    names = [n.strip() for n in ([row[1]] + (row[3] or "").split(",")) if n.strip()]
    return [n for n in names if all("\u4e00" <= ch <= "\u9fff" for ch in n)]


cities = []
for r in rows:
    if r[7] != "ADM2":
        continue
    zh = chinese_names(r)
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
if "city" in levels:
    cfg.SCOPE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出 %s：%d 个城市" % (cfg.SCOPE_FILE, len(out)))
    print("  ", [c["name_zh"] for c in out][:20])


if "county" in levels:
    counties = []
    for r in rows:
        if r[7] != "ADM3":
            continue
        zh = chinese_names(r)
        if not zh:
            continue
        raw_name = min(zh, key=len)
        name = raw_name
        if not name.endswith(("新区", "矿区", "林区")):
            for suffix in ("县", "区", "旗", "市"):
                if name.endswith(suffix) and len(name) > len(suffix) + 1:
                    name = name[:-len(suffix)]
                    break

        prov_row = adm1.get(r[10])
        prov_name = ""
        if prov_row:
            pz = chinese_names(prov_row)
            if pz:
                prov_name = min(pz, key=len).rstrip("省市")

        city_row = adm2.get(r[11])
        city_name = ""
        if city_row:
            cz = chinese_names(city_row)
            if cz:
                city_name = min(cz, key=len).rstrip("市")

        counties.append({
            "name_zh": name, "province": prov_name, "city": city_name,
            "county": name, "lat": float(r[4]), "lon": float(r[5]),
            "geonameid": int(r[0]), "population": int(r[14] or 0),
        })

    seen_counties, out_counties = set(), []
    for item in sorted(counties, key=lambda x: -x["population"]):
        key = (item["province"], item["city"], item["name_zh"])
        if key in seen_counties:
            continue
        seen_counties.add(key)
        out_counties.append(item)
    county_file = getattr(cfg, "SCOPE_COUNTIES_FILE",
                          cfg.DATA / "scope_counties.json")
    county_payload = {
        "generated_at": "2026-10-25",
        "source": "GeoNames CN.zip (CC BY 4.0)",
        "count": len(out_counties),
        "counties": out_counties,
    }
    county_file.write_text(
        json.dumps(county_payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("已写出 %s：%d 个县级地名" % (county_file, len(out_counties)))
    print("  ", [c["name_zh"] for c in out_counties][:20])
