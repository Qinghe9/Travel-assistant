#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单独验证高德公交/地铁路径规划（真实联网）。

用法：
    python scripts/check_amap_transit.py
    python scripts/check_amap_transit.py --origin 天河区 --destination 广州塔 --city 广州

说明：高德能返回地铁/公交线路与站点，但不提供 12306 高铁车次/余票。
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="高德公交/地铁前置检查")
ap.add_argument("--origin", default="天河区")
ap.add_argument("--destination", default="广州塔")
ap.add_argument("--city", default=None, help="目标城市（可选，用于歧义地点）")
a = ap.parse_args()

from app.rag import config  # noqa: E402,F401 - 触发 .env 加载
from app.tools.amap import AmapTransit, api_key  # noqa: E402

print("AMAP_API_KEY 已配置：%s" % ("是" if api_key() else "否"))
print("查询：%s → %s" % (a.origin, a.destination))

result = AmapTransit().run(origin=a.origin, destination_place=a.destination,
                           target_city=a.city)
print("状态：%s | 耗时：%s ms" % (result.status.value, result.latency_ms))
print("说明：%s" % (result.message or "-"))
print("数据：%s" % result.data)

plans = (result.data or {}).get("方案") or []
if result.status.value == "ok" and plans:
    print("\n通过：返回 %d 个公交/地铁方案" % len(plans))
    sys.exit(0)
print("\n失败：没有拿到公交/地铁方案")
sys.exit(1)
