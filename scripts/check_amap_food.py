#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单独验证高德餐饮 POI（真实联网）。

用法：
    python scripts/check_amap_food.py
    python scripts/check_amap_food.py --city 杭州 --area 西湖区
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="高德餐饮 POI 前置检查")
ap.add_argument("--city", default="杭州")
ap.add_argument("--area", default="西湖区")
a = ap.parse_args()

from app.rag import config  # noqa: E402,F401
from app.tools.amap import AmapFoodPOI, api_key  # noqa: E402

print("AMAP_API_KEY 已配置：%s" % ("是" if api_key() else "否"))
result = AmapFoodPOI().run(city=a.city, area=a.area)
print("状态：%s | 耗时：%sms" % (result.status.value, result.latency_ms))
print("说明：%s" % (result.message or "-"))
print("数据：%s" % result.data)

foods = (result.data or {}).get("餐饮") or []
if result.status.value == "ok" and foods:
    print("\n通过：返回 %d 家餐厅" % len(foods))
    sys.exit(0)
print("\n失败：没有拿到餐厅")
sys.exit(1)
