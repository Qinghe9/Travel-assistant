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
import threading
import time

from app.tools.base import Provider, make_ok, make_unavailable

BASE = "https://restapi.amap.com/v3"
_GEO_CACHE = {}
_GEO_LOCK = threading.RLock()
_GEO_INFLIGHT = {}
_GEO_TTL_SECONDS = 86400
_AMAP_RATE_LOCK = threading.Lock()
_AMAP_LAST_CALL = [0.0]
_AMAP_MIN_INTERVAL = float(os.environ.get("AMAP_MIN_INTERVAL", "0.36"))


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
    # 个人 Key 通常只有约 3 次/秒额度。工具并行时统一错开请求起点，
    # 既保留并行等待收益，又避免 CUQPS_HAS_EXCEEDED_THE_LIMIT。
    with _AMAP_RATE_LOCK:
        now = time.monotonic()
        wait = _AMAP_MIN_INTERVAL - (now - _AMAP_LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        _AMAP_LAST_CALL[0] = time.monotonic()
    query = dict(params or {})
    query["key"] = api_key()
    resp = httpx.get(BASE + path, params=query, timeout=timeout)
    data = resp.json()
    if str(data.get("status")) != "1":
        raise AmapError(str(data.get("info") or ""), str(data.get("infocode") or ""))
    return data


def geocode(address: str, city: str = None) -> dict:
    """地名 -> {location: "lon,lat", adcode, city, formatted_address}。"""
    cache_key = ((address or "").strip(), (city or "").strip())
    now = time.time()
    owner = False
    with _GEO_LOCK:
        cached = _GEO_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return dict(cached[1])
        inflight = _GEO_INFLIGHT.get(cache_key)
        if inflight is None:
            inflight = [threading.Event(), None, None]
            _GEO_INFLIGHT[cache_key] = inflight
            owner = True
    if not owner:
        # 驾车、公交并行时可能同时解析“西安/北京”。只有一个线程发请求，
        # 其他线程等到结果后直接复用，避免重复占用 QPS。
        if not inflight[0].wait(timeout=12):
            raise AmapError("等待地理编码超时：%s" % address, "geocode_timeout")
        if inflight[2] is not None:
            raise inflight[2]
        return dict(inflight[1] or {})

    params = {"address": address, "output": "JSON"}
    if city:
        params["city"] = city
    try:
        data = _get("/geocode/geo", params)
        rows = data.get("geocodes") or []
        if not rows:
            raise AmapError("没有解析到地址：%s" % address, "empty_geocode")
        row = rows[0]
        result = {
            "location": row.get("location", ""),
            "adcode": row.get("adcode", ""),
            "city": row.get("city") or city or "",
            "formatted_address": row.get("formatted_address", ""),
        }
        with _GEO_LOCK:
            _GEO_CACHE[cache_key] = (time.time() + _GEO_TTL_SECONDS, result)
        inflight[1] = result
        return dict(result)
    except Exception as exc:
        inflight[2] = exc
        raise
    finally:
        with _GEO_LOCK:
            _GEO_INFLIGHT.pop(cache_key, None)
        inflight[0].set()


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


class AmapHotelPOI(Provider):
    """酒店/住宿 POI 搜索：先地理编码区域，再周边检索。

    高德类型码（实测）：
        100000 住宿服务（大类）
        100100 宾馆酒店
        100200 旅馆招待所
    注意：网传的 141201 实际属于科教文化类，会返回大学，不能用于酒店。
    """

    name = "amap_poi_hotel_search"
    ttl = 3600
    timeout = 4.0

    def fetch(self, city: str = None, area: str = None, keyword: str = None):
        if not api_key():
            return make_unavailable(self.name, "未配置 AMAP_API_KEY，酒店接口暂不可用",
                                    "no_api_key")
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        center = (area or keyword or city).strip()
        try:
            geo = geocode(center, city=city)
        except AmapError as exc:
            return make_unavailable(self.name, "区域解析失败：%s" % exc, exc.code or "geo_error")

        request = {
            "city": city,
            "area": center,
            "location": geo.get("location"),
            "radius": 8000,
            "types": "100000|100100|100200",
            "offset": 10,
            "page": 1,
        }
        print("[amap-hotel] request=%s" % request, flush=True)
        data = _get("/place/around", {
            "location": request["location"], "radius": request["radius"],
            "types": request["types"], "offset": request["offset"],
            "page": request["page"], "extensions": "base", "output": "JSON",
        }, timeout=self.timeout)

        hotels = []
        for row in data.get("pois") or []:
            type_text = row.get("type") or ""
            # 只认主类型为「住宿服务」；混合类型的酒家/餐厅会被排除
            if (type_text.split(";")[0] or "").strip() != "住宿服务":
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            district = (row.get("adname") or "").strip()
            address = row.get("address")
            if isinstance(address, list):
                address = "".join(str(x) for x in address)
            address = (address or "").strip()
            extra = "，".join(x for x in (district, address) if x)
            hotels.append("%s（%s）" % (name, extra) if extra else name)
        hotels = list(dict.fromkeys(hotels))
        print("[amap-hotel] names=%s" % hotels[:10], flush=True)
        if not hotels:
            return make_unavailable(self.name, "该区域暂时没有返回酒店：%s" % center,
                                    "empty_response")
        return make_ok(self.name, {
            "城市": city,
            "区域": center,
            "酒店": hotels[:8],
            "数据来源": "高德地图",
        }, "获取成功（仅名称，无价格/房型）", ttl=self.ttl)


class AmapFoodPOI(Provider):
    """餐饮 POI 搜索：高德类型 050000（餐饮服务）。

    只返回餐厅名称/区域/地址，不提供菜品、评分、人均价格。
    菜品/小吃仍由 RAG 知识库补充。
    """

    name = "amap_poi_food_search"
    ttl = 3600
    timeout = 4.0

    def fetch(self, city: str = None, area: str = None, keyword: str = None):
        if not api_key():
            return make_unavailable(self.name, "未配置 AMAP_API_KEY，餐饮接口暂不可用",
                                    "no_api_key")
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        center = (area or keyword or city).strip()
        try:
            geo = geocode(center, city=city)
        except AmapError as exc:
            return make_unavailable(self.name, "区域解析失败：%s" % exc,
                                    exc.code or "geo_error")
        request = {"city": city, "area": center, "location": geo.get("location"),
                   "radius": 5000, "types": "050000", "offset": 10, "page": 1}
        print("[amap-food] request=%s" % request, flush=True)
        data = _get("/place/around", {
            "location": geo["location"], "radius": request["radius"],
            "types": request["types"], "offset": request["offset"],
            "page": request["page"], "extensions": "base", "output": "JSON",
        }, timeout=self.timeout)
        foods = []
        for row in data.get("pois") or []:
            type_text = row.get("type") or ""
            if (type_text.split(";")[0] or "").strip() != "餐饮服务":
                continue
            name = (row.get("name") or "").strip()
            if not name:
                continue
            district = (row.get("adname") or "").strip()
            address = row.get("address")
            if isinstance(address, list):
                address = "".join(str(x) for x in address)
            address = (address or "").strip()
            extra = "，".join(x for x in (district, address) if x)
            foods.append("%s（%s）" % (name, extra) if extra else name)
        foods = list(dict.fromkeys(foods))
        print("[amap-food] names=%s" % foods[:10], flush=True)
        if not foods:
            return make_unavailable(self.name, "该区域暂时没有返回餐饮：%s" % center,
                                    "empty_response")
        return make_ok(self.name, {
            "城市": city,
            "区域": center,
            "餐饮": foods[:8],
            "数据来源": "高德地图",
        }, "获取成功（仅餐厅名称/地址，无菜品与价格）", ttl=self.ttl)


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


class AmapTransit(Provider):
    """公交/地铁路径规划（市内换乘；跨城会返回城际+地铁组合）。

    注意：高德没有开放 12306 车次/余票接口；本工具不会编造高铁车次。
    """

    name = "amap_transit_route"
    ttl = 600
    timeout = 5.0

    def fetch(self, origin: str = None, destination_place: str = None,
              target_city: str = None):
        if not api_key():
            return make_unavailable(self.name, "未配置 AMAP_API_KEY，公交/地铁查询暂不可用",
                                    "no_api_key")
        if not (origin and destination_place):
            return make_unavailable(self.name, "缺少起点或终点", "bad_request")
        try:
            geo_o = geocode(origin)
            geo_d = geocode(destination_place, city=target_city)
        except AmapError as exc:
            return make_unavailable(self.name, "地点解析失败：%s" % exc,
                                    exc.code or "geo_error")

        city_start = geo_o.get("city") or ""
        city_end = target_city or geo_d.get("city") or city_start
        request = {
            "origin": origin, "destination": destination_place,
            "city": city_start, "cityd": city_end,
            "origin_location": geo_o.get("location"),
            "dest_location": geo_d.get("location"),
        }
        print("[amap-transit] request=%s" % request, flush=True)
        data = _get("/direction/transit/integrated", {
            "origin": geo_o["location"], "destination": geo_d["location"],
            "city": city_start, "cityd": city_end, "strategy": 0,
            "nightflag": 0, "extensions": "base", "output": "JSON",
        }, timeout=self.timeout)

        transits = ((data.get("route") or {}).get("transits") or [])
        if not transits:
            return make_unavailable(self.name, "没有找到公交/地铁方案：%s → %s"
                                    % (origin, destination_place), "empty_response")

        plans = []
        for idx, item in enumerate(transits[:2], start=1):
            parts = []
            for seg in item.get("segments") or []:
                walking = seg.get("walking") or {}
                if walking.get("distance"):
                    parts.append("步行%s米" % walking.get("distance"))
                for line in ((seg.get("bus") or {}).get("buslines") or []):
                    name = line.get("name") or ""
                    dep = (line.get("departure_stop") or {}).get("name") or ""
                    arr = (line.get("arrival_stop") or {}).get("name") or ""
                    if name:
                        parts.append("%s（%s→%s）" % (name, dep, arr))
                railway = seg.get("railway") or {}
                if railway.get("name") or railway.get("trip"):
                    parts.append(("%s %s" % (railway.get("name") or "",
                                             railway.get("trip") or "")).strip())
            try:
                km = round(float(item.get("distance") or 0) / 1000.0, 1)
                minutes = round(float(item.get("duration") or 0) / 60.0)
            except (TypeError, ValueError):
                km, minutes = 0.0, 0
            cost = item.get("cost")
            plans.append("方案%d：约 %.1f 公里，约 %d 分钟，票价约 %s 元；%s"
                         % (idx, km, minutes, cost if cost not in (None, "") else "未知",
                            " → ".join(parts) if parts else "步行"))

        print("[amap-transit] plans=%s" % plans, flush=True)
        return make_ok(self.name, {
            "起点": origin,
            "终点": destination_place,
            "方案": plans,
            "说明": "公交/地铁方案来自高德；不含高铁车次与余票，跨城铁路请以 12306 为准",
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
