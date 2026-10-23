# -*- coding: utf-8 -*-
"""高德地图 Web 服务适配器：景点 POI、路线距离、天气。

一个 Key 覆盖三种能力（需在控制台为 Key 开通「Web服务」）：
    /v3/place/text        关键词/分类 POI 搜索
    /v3/geocode/geo       地名 -> 经纬度 + adcode
    /v3/direction/driving 驾车路径规划
    /v3/weather/weatherInfo 实况与预报

Key 只从环境变量 AMAP_API_KEY 读取，绝不写进代码或仓库。
"""

from __future__ import annotations

import os

from app.tools.base import Provider, make_ok, make_unavailable

BASE = "https://restapi.amap.com/v3"


class AmapError(RuntimeError):
    def __init__(self, info: str, code: str = ""):
        super().__init__("%s（infocode=%s）" % (info or "高德接口返回错误", code or "-"))
        self.info = info
        self.code = code


def api_key() -> str:
    return os.environ.get("AMAP_API_KEY", "").strip()


def _get(path: str, params: dict, timeout: float = 4.0) -> dict:
    """调用高德 Web 服务；status != 1 时抛 AmapError。"""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("缺少 httpx 依赖：pip install httpx") from exc
    if not api_key():
        raise AmapError("未配置 AMAP_API_KEY", "no_key")
    query = dict(params or {})
    query["key"] = api_key()
    resp = httpx.get(BASE + path, params=query, timeout=timeout)
    data = resp.json()
    if str(data.get("status")) != "1":
        raise AmapError(str(data.get("info") or ""), str(data.get("infocode") or ""))
    return data


def geocode(address: str, city: str = None) -> dict:
    """地名 -> {location: "lon,lat", adcode, city, formatted_address}。"""
    params = {"address": address, "output": "JSON"}
    if city:
        params["city"] = city
    data = _get("/geocode/geo", params)
    rows = data.get("geocodes") or []
    if not rows:
        raise AmapError("没有解析到地址：%s" % address, "empty_geocode")
    row = rows[0]
    return {
        "location": row.get("location", ""),
        "adcode": row.get("adcode", ""),
        "city": row.get("city") or city or "",
        "formatted_address": row.get("formatted_address", ""),
    }


class AmapPOI(Provider):
    """景点/兴趣点搜索。"""

    name = "amap-poi"
    ttl = 86400
    timeout = 4.0

    def fetch(self, city: str = None, entity: str = None):
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        keywords = (entity or "").strip()
        params = {
            "city": city, "citylimit": "true", "sortrule": "weight",
            "offset": 10, "page": 1, "extensions": "base", "output": "JSON",
        }
        if keywords:
            # 用户点名了具体地点：按关键词查
            params["keywords"] = keywords
        else:
            # 泛问“有什么好玩的”：只取国家级/省级景点，避免搜出一堆广场
            params["types"] = "110202|110203"
        data = _get("/place/text", params, timeout=self.timeout)
        rows = data.get("pois") or []
        spots = []
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            area = row.get("adname") or ""
            address = row.get("address")
            if isinstance(address, list):
                address = "".join(str(x) for x in address)
            address = (address or "").strip()
            tag = "（%s%s）" % (area, ("，" + address) if address else "")
            spots.append(name + tag)
        if not spots:
            return make_unavailable(self.name, "高德没有返回可用景点：%s" % keywords,
                                    "empty_response")
        return make_ok(self.name, {
            "城市": city,
            "关键词": keywords,
            "景点": spots[:8],
            "数据来源": "高德地图",
        }, "获取成功", ttl=self.ttl)


class AmapRoute(Provider):
    """两地之间的驾车/步行距离与预计耗时。"""

    name = "amap-route"
    ttl = 86400
    timeout = 4.0

    def fetch(self, start_city: str = None, target_city: str = None, mode: str = "driving"):
        if not (start_city and target_city):
            return make_unavailable(self.name, "缺少起点或终点", "bad_request")
        if mode not in ("driving", "walking"):
            mode = "driving"
        start = geocode(start_city)
        target = geocode(target_city)
        data = _get("/direction/%s" % mode, {
            "origin": start["location"], "destination": target["location"],
            "extensions": "base", "output": "JSON",
        }, timeout=self.timeout)
        paths = ((data.get("route") or {}).get("paths") or [])
        if not paths:
            return make_unavailable(self.name, "高德没有返回可用路线", "empty_response")
        path = paths[0]
        try:
            distance_km = round(float(path.get("distance") or 0) / 1000.0, 1)
            minutes = round(float(path.get("duration") or 0) / 60.0)
        except (TypeError, ValueError):
            return make_unavailable(self.name, "路线数据格式异常", "bad_response")
        return make_ok(self.name, {
            "起点": start_city,
            "终点": target_city,
            "出行方式": "驾车" if mode == "driving" else "步行",
            "距离(公里)": distance_km,
            "预计耗时(分钟)": minutes,
            "数据来源": "高德地图",
        }, "获取成功", ttl=self.ttl)


class AmapWeather(Provider):
    """高德天气：实况或 4 天预报（启用方式：WEATHER_PROVIDER=amap）。"""

    name = "amap-weather"
    ttl = 1800
    timeout = 4.0

    def fetch(self, city: str = None, date: str = None, end_date: str = None, days: int = None):
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        geo = geocode(city)
        adcode = geo.get("adcode")
        if not adcode:
            return make_unavailable(self.name, "没有解析到城市编码：%s" % city, "no_adcode")
        if date:
            data = _get("/weather/weatherInfo", {
                "city": adcode, "extensions": "all", "output": "JSON",
            }, timeout=self.timeout)
            forecasts = data.get("forecasts") or []
            casts = (forecasts[0].get("casts") if forecasts else []) or []
            if end_date or days:
                import datetime as _dt
                stop = end_date
                if not stop and days:
                    try:
                        stop = (_dt.date.fromisoformat(date)
                                + _dt.timedelta(days=max(int(days), 1) - 1)).isoformat()
                    except ValueError:
                        stop = date
                if casts and (date < (casts[0].get("date") or "")
                              or (stop and stop > (casts[-1].get("date") or ""))):
                    window = "%s ~ %s" % (casts[0].get("date"), casts[-1].get("date"))
                    return make_unavailable(self.name, "只能查询 %s 的预报" % window,
                                            "out_of_range")
                picked_rows = [c for c in casts if date <= (c.get("date") or "") <= (stop or date)]
                if not picked_rows:
                    window = "%s ~ %s" % (casts[0].get("date"), casts[-1].get("date")) if casts else "未知"
                    return make_unavailable(self.name, "只能查询 %s 的预报" % window,
                                            "out_of_range")
                return make_ok(self.name, {
                    "城市": geo.get("city") or city,
                    "预报": ["%s：白天%s，夜间%s，%s~%s℃"
                             % (c.get("date"), c.get("dayweather"), c.get("nightweather"),
                                c.get("nighttemp"), c.get("daytemp")) for c in picked_rows],
                    "发布时间": forecasts[0].get("reporttime") if forecasts else "",
                    "数据来源": "高德地图",
                }, "区间预报获取成功", ttl=self.ttl)
            picked = next((c for c in casts if c.get("date") == date), None)
            if picked is None:
                window = "%s ~ %s" % (casts[0].get("date"), casts[-1].get("date")) if casts else "未知"
                return make_unavailable(self.name, "只能查询 %s 的预报" % window,
                                        "out_of_range")
            return make_ok(self.name, {
                "城市": geo.get("city") or city,
                "日期": picked.get("date"),
                "白天天气": picked.get("dayweather"),
                "夜间天气": picked.get("nightweather"),
                "最高温(℃)": picked.get("daytemp"),
                "最低温(℃)": picked.get("nighttemp"),
                "白天风向": picked.get("daywind"),
                "白天风力": picked.get("daypower"),
                "发布时间": forecasts[0].get("reporttime") if forecasts else "",
                "数据来源": "高德地图",
            }, "预报获取成功", ttl=self.ttl)

        data = _get("/weather/weatherInfo", {
            "city": adcode, "extensions": "base", "output": "JSON",
        }, timeout=self.timeout)
        lives = data.get("lives") or []
        if not lives:
            return make_unavailable(self.name, "高德没有返回实况天气", "empty_response")
        live = lives[0]
        return make_ok(self.name, {
            "城市": live.get("city") or city,
            "天气": live.get("weather"),
            "温度(℃)": live.get("temperature"),
            "风向": live.get("winddirection"),
            "风力": live.get("windpower"),
            "湿度(%)": live.get("humidity"),
            "发布时间": live.get("reporttime"),
            "数据来源": "高德地图",
        }, "实况获取成功", ttl=self.ttl)
