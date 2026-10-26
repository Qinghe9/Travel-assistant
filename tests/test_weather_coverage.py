import datetime as dt

import httpx
import pytest

from app.tools import amap
from app.tools import weather
from app.tools.base import make_ok


class Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


@pytest.mark.parametrize("city,lat,lon", [
    ("安顺", 26.25, 105.95),
    ("凯里", 26.57, 107.98),
    ("婺源", 29.25, 117.86),
])
def test_small_city_weather_uses_resolved_coordinates(monkeypatch, city, lat, lon):
    seen = {}

    def fake_resolve(name, trace=None):
        return {"name": name, "province": "测试省", "city": name, "county": name,
                "adcode": "", "lat": lat, "lon": lon, "source": "geonames"}

    def fake_get(url, params=None, timeout=None):
        seen.update(params)
        start = dt.date.today()
        days = params["forecast_days"]
        dates = [(start + dt.timedelta(days=i)).isoformat() for i in range(days)]
        return Response({"daily": {
            "time": dates, "temperature_2m_max": [25] * days,
            "temperature_2m_min": [18] * days,
            "weather_code": [3] * days,
            "precipitation_probability_max": [0] * days,
        }})

    monkeypatch.setattr(weather, "resolve_place", fake_resolve)
    monkeypatch.setattr(httpx, "get", fake_get)
    result = weather.OpenMeteoWeather().fetch(city=city, days=3)
    assert result.status.value == "ok"
    assert seen["latitude"] == lat
    assert seen["longitude"] == lon
    assert result.data["_geocode"]["source"] == "geonames"


def test_unknown_place_is_unavailable_and_names_input(monkeypatch):
    monkeypatch.setattr(weather, "resolve_place", lambda name, trace=None: None)
    result = weather.OpenMeteoWeather().fetch(city="不存在的地名")
    assert result.status.value == "unavailable"
    assert result.error_code == "place_not_found"
    assert "不存在的地名" in result.message


def test_amap_weather_falls_back_to_coordinates_without_adcode(monkeypatch):
    monkeypatch.setattr(amap, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        "app.agent.geocode.resolve_place",
        lambda name, trace=None: {
            "name": name, "province": "江西", "city": "上饶", "county": name,
            "adcode": "", "lat": 29.25, "lon": 117.86, "source": "geonames",
        },
    )
    monkeypatch.setattr(
        weather.OpenMeteoWeather, "fetch",
        lambda self, **kwargs: make_ok("open-meteo", {"城市": kwargs["city"]}),
    )
    result = amap.AmapWeather().fetch(city="婺源", days=3)
    assert result.status.value == "ok"
    assert "Open-Meteo" in result.data["高德天气降级说明"]
