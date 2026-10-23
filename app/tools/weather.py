# -*- coding: utf-8 -*-
"""天气工具：Open-Meteo（无需 API Key）。

PRD 第 6.1 节把 Open-Meteo 列为天气数据源；FR-05 要求
「失败时给出可恢复提示，不生成替代数据」。
"""

from __future__ import annotations

import datetime as _dt

from app.tools.base import Provider, make_ok, make_unavailable

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO 天气代码 -> 中文（Open-Meteo 官方分类）
WMO = {
    0: "晴", 1: "大致晴朗", 2: "局部多云", 3: "阴",
    45: "雾", 48: "雾凇",
    51: "毛毛雨", 53: "小雨", 55: "中雨", 56: "冻毛毛雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "强冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "强阵雨", 82: "暴雨",
    85: "阵雪", 86: "强阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


def describe(code) -> str:
    try:
        return WMO.get(int(code), "天气代码 %s" % code)
    except (TypeError, ValueError):
        return "天气代码 %s" % code


class OpenMeteoWeather(Provider):
    """Open-Meteo 天气适配器：先地理编码，再取预报。"""

    name = "open-meteo"
    ttl = 1800
    timeout = 3.0

    def fetch(self, city: str = None, date: str = None, end_date: str = None, days: int = None):
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        try:
            import httpx
        except ImportError:
            return make_unavailable(self.name, "缺少 httpx 依赖：pip install httpx", "no_dependency")
        geo = httpx.get(GEOCODE_URL,
                        params={"name": city, "count": 1, "language": "zh"},
                        timeout=self.timeout).json()
        results = geo.get("results") or []
        if not results:
            return make_unavailable(self.name, "没有找到城市：%s" % city, "city_not_found")
        loc = results[0]
        need = 7
        try:
            if date and end_date:
                need = max(need, (_dt.date.fromisoformat(end_date)
                                  - _dt.date.fromisoformat(date)).days + 1)
            elif date and days:
                need = max(need, int(days))
        except (TypeError, ValueError):
            pass
        forecast = httpx.get(FORECAST_URL, params={
            "latitude": loc["latitude"], "longitude": loc["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "current": "temperature_2m,weather_code",
            "timezone": "Asia/Shanghai", "forecast_days": min(need + 1, 16),
        }, timeout=self.timeout).json()
        daily = forecast.get("daily") or {}
        rows = list(zip(daily.get("time", []),
                        daily.get("temperature_2m_max", []),
                        daily.get("temperature_2m_min", []),
                        daily.get("weather_code", []),
                        daily.get("precipitation_probability_max", [])))
        if not rows:
            return make_unavailable(self.name, "天气服务未返回预报数据", "empty_response")

        # 区间查询：返回区间内的每一天
        if date and (end_date or days):
            stop = end_date
            if not stop and days:
                try:
                    stop = (_dt.date.fromisoformat(date)
                            + _dt.timedelta(days=max(int(days), 1) - 1)).isoformat()
                except ValueError:
                    stop = date
            if date < rows[0][0] or (stop and stop > rows[-1][0]):
                return make_unavailable(self.name,
                                        "只能查询 %s 至 %s 之间的预报" % (rows[0][0], rows[-1][0]),
                                        "out_of_range")
            picked_rows = [r for r in rows if date <= r[0] <= (stop or date)]
            if not picked_rows:
                return make_unavailable(self.name,
                                        "只能查询 %s 至 %s 之间的预报" % (rows[0][0], rows[-1][0]),
                                        "out_of_range")
            return make_ok(self.name, {
                "城市": loc.get("name") or city,
                "省/州": loc.get("admin1") or "",
                "预报": ["%s：%s，最高 %s℃，最低 %s℃，降水概率 %s%%"
                         % (r[0], describe(r[3]), r[1], r[2], r[4]) for r in picked_rows],
                "数据来源": "Open-Meteo",
            }, "区间预报获取成功", ttl=self.ttl)

        target = date or _dt.date.today().isoformat()
        picked = next((d for d in rows if d[0] == target), None)
        if date and picked is None:
            return make_unavailable(self.name,
                                    "只能查询 %s 至 %s 之间的预报" % (rows[0][0], rows[-1][0]),
                                    "out_of_range")
        if picked is None:
            picked = rows[0]
        current = forecast.get("current") or {}
        data = {
            "城市": loc.get("name") or city,
            "省/州": loc.get("admin1") or "",
            "日期": picked[0],
            "天气": describe(picked[3]),
            "最高温(℃)": picked[1],
            "最低温(℃)": picked[2],
            "降水概率(%)": picked[4],
            "当前温度(℃)": current.get("temperature_2m"),
            "数据来源": "Open-Meteo",
        }
        return make_ok(self.name, data, "预报获取成功", ttl=self.ttl)
