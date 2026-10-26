#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单独验证高德酒店 POI 接口（真实联网）。

用法：
    python scripts/check_amap_hotel.py
    python scripts/check_amap_hotel.py --city 广州 --area 天河区
    python scripts/check_amap_hotel.py --city 广州 --keyword 珠江新城

校验：AMAP_API_KEY 是否有效、地理编码是否成功、周边检索是否返回酒店名称。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="高德酒店 POI 前置检查")
ap.add_argument("--city", default="广州")
ap.add_argument("--area", default="天河区")
ap.add_argument("--keyword", default=None)
a = ap.parse_args()

from app.rag import config  # noqa: E402,F401  - 触发 .env 加载
from app.tools.amap import AmapHotelPOI, api_key  # noqa: E402

print("AMAP_API_KEY 已配置：%s" % ("是" if api_key() else "否"))
print("查询：城市=%s 区域=%s 关键词=%s" % (a.city, a.area, a.keyword))

result = AmapHotelPOI().run(city=a.city, area=a.area, keyword=a.keyword)
print("状态：%s | 耗时：%s ms" % (result.status.value, result.latency_ms))
print("说明：%s" % (result.message or "-"))
print("数据：%s" % result.data)

hotels = (result.data or {}).get("酒店") or []
if result.status.value == "ok" and hotels:
    print("\n通过：返回 %d 家酒店" % len(hotels))
    sys.exit(0)
print("\n失败：没有拿到酒店名称")
sys.exit(1)
