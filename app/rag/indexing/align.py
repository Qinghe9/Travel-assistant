# -*- coding: utf-8 -*-
"""地名对齐：用 GeoNames alternatenames 建 aliases.json。

说明：别名来源仅 GeoNames（CC BY 4.0）+ 人工整理的旅行别称；
未纳入 Wikidata label/alias（端点在本机网络下不可达）。
"""

from __future__ import annotations

import io
import re
import zipfile
from collections import defaultdict

CJK = re.compile(r"[\u4e00-\u9fff]{2,}")
EN = re.compile(r"[A-Za-z][A-Za-z .'\-]{2,}")


def iter_geonames(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        with z.open("CN.txt") as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                c = line.rstrip("\n").split("\t")
                if len(c) >= 19:
                    yield c


def zh_names(row) -> set:
    out = set()
    for n in [row[1]] + (row[3].split(",") if row[3] else []):
        n = n.strip()
        if n and CJK.fullmatch(n):
            out.add(n)
    return out


def build_aliases(zip_path, cities: dict, corpus_text: str, admin1_codes: set, hand_curated: dict):
    """返回 (别名->规范名, 规范名->别名列表, 候选行数, 命中行数)。只保留语料中出现的规范名。"""
    alias2canon, canon2alias = dict(hand_curated), defaultdict(set)
    for a, c in hand_curated.items():
        canon2alias[c].add(a)
    prov_rows = kept = 0
    for row in iter_geonames(zip_path):
        if row[10] not in admin1_codes:
            continue
        if row[6] not in ("A", "P") or not (row[7].startswith("ADM") or row[7].startswith("PPL")):
            continue
        prov_rows += 1
        names = zh_names(row)
        if not names:
            continue
        canon = sorted(names, key=len)[0]
        if canon not in corpus_text:
            continue
        kept += 1
        for n in names:
            alias2canon.setdefault(n, canon)
            canon2alias[canon].add(n)
        if row[2]:
            canon2alias[canon].add(row[2])
    return alias2canon, {k: sorted(v) for k, v in canon2alias.items()}, prov_rows, kept


HAND_CURATED = {
    "蓉城": "成都", "锦官城": "成都", "蓉": "成都",
    "魔都": "上海", "沪": "上海", "申城": "上海", "浦東": "浦东", "浦东新区": "浦东",
    "羊城": "广州", "穗": "广州", "花城": "广州",
    "鹏城": "深圳", "鹏": "深圳",
    "山城": "重庆", "渝": "重庆", "雾都": "重庆",
    "江城": "武汉", "汉": "武汉", "汉口": "武汉", "武昌": "武汉", "汉阳": "武汉",
    "六朝古都": "南京", "金陵": "南京", "宁": "南京",
    "姑苏": "苏州", "吴中": "苏州", "平江": "苏州",
    "杭城": "杭州", "临安": "杭州",
    "京城": "北京", "首都": "北京", "燕京": "北京", "京师": "北京",
}
