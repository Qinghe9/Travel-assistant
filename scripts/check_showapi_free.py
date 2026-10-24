#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""万维易源免费旅游接口前置检查：节假日 + 城市空气质量。

用法：
    python scripts/check_showapi_free.py
    python scripts/check_showapi_free.py --date 2026-10-01 --city 广州
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ap = argparse.ArgumentParser(description="ShowAPI 免费旅游接口检查")
ap.add_argument("--date", default="2026-10-01")
ap.add_argument("--city", default="广州")
a = ap.parse_args()

from app.rag import config  # noqa: E402,F401 - 触发 .env 加载
from app.tools.showapi import ShowAPIAirQuality, ShowAPIHoliday, app_key  # noqa: E402

print("SHOWAPI_APP_KEY 已配置：%s" % ("是" if app_key() else "否"))

holiday = ShowAPIHoliday().run(date=a.date)
print("\n[节假日] 状态=%s 耗时=%sms" % (holiday.status.value, holiday.latency_ms))
print(holiday.data)

air = ShowAPIAirQuality().run(city=a.city)
print("\n[空气质量] 状态=%s 耗时=%sms" % (air.status.value, air.latency_ms))
print(air.data)

ok = holiday.status.value == "ok" and air.status.value == "ok"
print("\n%s" % ("通过：两个免费接口都可用" if ok else "失败：至少一个接口不可用"))
sys.exit(0 if ok else 1)
