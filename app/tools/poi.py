# -*- coding: utf-8 -*-
"""景点/POI 工具：OpenTripMap。

实现依据 OpenTripMap 公开文档（geoname -> radius 两步查询）。
说明：本适配器在本地未做联网验证（CI 不联网），首次启用请手工跑一次确认。
未配置 OPENTRIPMAP_API_KEY 时如实返回 unavailable，不返回任何占位数据。
"""

from __future__ import annotations

import os

from app.tools.base import Provider, make_ok, make_unavailable

API_ROOT = "https://api.opentripmap.com/0.1/en/places"


class OpenTripMapPOI(Provider):
    """按城市查附近的兴趣点。"""

    name = "opentripmap"
    ttl = 86400
    timeout = 4.0

    @property
    def api_key(self) -> str:
        return os.environ.get("OPENTRIPMAP_API_KEY", "").strip()

    def fetch(self, city: str = None, entity: str = None):
        if not self.api_key:
            return make_unavailable(self.name, "未配置 OPENTRIPMAP_API_KEY，景点接口暂不可用",
                                    "no_api_key")
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        try:
            import httpx
        except ImportError:
            return make_unavailable(self.name, "缺少 httpx 依赖：pip install httpx", "no_dependency")
        geo = httpx.get(API_ROOT + "/geoname",
                        params={"name": city, "apikey": self.api_key},
                        timeout=self.timeout).json()
        if not geo.get("lat"):
            return make_unavailable(self.name, "没有找到城市：%s" % city, "city_not_found")
        rows = httpx.get(API_ROOT + "/radius", params={
            "radius": 8000, "lat": geo["lat"], "lon": geo["lon"],
            "kinds": "interesting_places", "rate": 2, "format": "json",
            "limit": 10, "apikey": self.api_key,
        }, timeout=self.timeout).json()
        spots = []
        for item in (rows or [])[:10]:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            if entity and entity not in name and name not in entity:
                continue
            spots.append(name)
        if not spots:
            return make_unavailable(self.name, "该城市暂时没有返回可用景点", "empty_response")
        return make_ok(self.name, {"城市": city, "推荐景点": spots, "数据来源": "OpenTripMap"},
                       "获取成功", ttl=self.ttl)
