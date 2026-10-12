#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 data/manifest.csv 生成 NOTICE（署名与许可清单）。

用法：
    python scripts/gen_notice.py
"""

from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.rag import config as cfg  # noqa: E402

MANIFEST = cfg.DATA / "manifest.csv"
rows = list(csv.DictReader(MANIFEST.open(encoding="utf-8")))
print("manifest 行数 %d" % len(rows))

by_source = defaultdict(list)
for r in rows:
    by_source[r["source"]].append(r)

SOURCE_NOTE = {
    "zhwikivoyage": "中文维基导游（Wikivoyage）条目文本。本作品（或其部分）采用 CC BY-SA 4.0 许可。",
    "enwikivoyage": "英文维基导游（Wikivoyage）条目文本，用于交叉核对地名与景点译名。CC BY-SA 4.0。",
    "zhwikipedia": "中文维基百科条目文本（经 dump 分片按标题抓取）。CC BY-SA 4.0。",
    "geonames-cn": "GeoNames 中国地名数据（CN.zip）。CC BY 4.0。",
    "geonames-cities": "GeoNames cities 数据集（人口 > 500 的城市）。CC BY 4.0。",
    "geonames-admin": "GeoNames 行政区划与别名（admin1CodesASCII / alternateNames）。CC BY 4.0。",
    "chinatravel-sandbox-zh": "ChineseTravel 中文旅行对话语料（评测与指令格式参考）。",
    "chinatravel-queries": "ChineseTravel 查询集（意图识别评测）。",
    "ms-address-ner": "ModelScope 中文地址 NER 数据集（地名切分校验）。",
    "ms-geoglue-spec": "ModelScope GeoGLUE 地理实体/规范名数据集。CC BY-NC 4.0（仅课程研究使用，不得商用）。",
    "ms-poi-national": "ModelScope 全国 POI 数据集（景点/餐饮/住宿类别）。",
    "ms-travel-dialogue": "ModelScope 中文旅游对话数据集（多轮指令参考）。",
    "ms-touind-qa": "ModelScope 旅游意图问答数据集（意图与槽位校验）。",
}

lines = [
    "旅游规划助手 · RAG 模块 NOTICE",
    "=" * 60,
    "",
    "本文件由 scripts/gen_notice.py 从 data/manifest.csv 自动生成，请勿手工编辑。",
    "生成时间：%s" % datetime.now().astimezone().isoformat(timespec="seconds"),
    "数据源数量：%d    文件条目：%d" % (len(by_source), len(rows)),
    "",
    "所有第三方数据仅用于课程设计中的检索增强生成（RAG）知识库构建，",
    "不做二次分发；CC BY-SA 类语料的衍生文本同样以 CC BY-SA 4.0 提供。",
    "",
]

for src in sorted(by_source):
    rs = by_source[src]
    lic = sorted({r.get("license", "") for r in rs if r.get("license")})
    lines += ["-" * 60, "%s" % src, "-" * 60,
              "许可：%s" % ("、".join(lic) or "见来源页面"),
              "文件数：%d" % len(rs)]
    if SOURCE_NOTE.get(src):
        lines += ["说明：%s" % SOURCE_NOTE[src]]
    date = sorted({r.get("retrieved_at", "") for r in rs if r.get("retrieved_at")})
    if date:
        lines.append("获取时间：%s" % "、".join(date))
    urls = []
    for r in rs:
        u = r.get("source_url") or ""
        if u and u not in urls:
            urls.append(u)
    for u in urls[:3]:
        lines.append("来源：%s" % u)
    if len(urls) > 3:
        lines.append("（另有 %d 个来源链接）" % (len(urls) - 3))
    lines.append("")

lines += ["-" * 60, "引用方式", "-" * 60,
          "若在报告、论文或演示中引用本知识库，请同时保留各来源的署名与许可声明，",
          "例如：本回答依据中文维基导游（CC BY-SA 4.0）与 GeoNames（CC BY 4.0）资料。", ""]

(ROOT / "NOTICE").write_text("\n".join(lines), encoding="utf-8")
print("已写出 %s（%d 行 / %d 个数据源）" % (ROOT / "NOTICE", len(lines), len(by_source)))
