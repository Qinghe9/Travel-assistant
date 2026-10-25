import httpx
import pytest

from app.agent import nodes
from app.tools import weather


class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


@pytest.mark.parametrize("days", [2, 3, 7])
def test_weather_days_without_date_returns_multi_day(monkeypatch, days):
    calls = []
    monkeypatch.setattr(weather, "resolve_place", lambda name, trace=None: {
        "name": "澳门", "province": "澳门", "city": "澳门", "county": "",
        "adcode": "820000", "lat": 22.2, "lon": 113.5, "source": "scope",
    })

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        days = params["forecast_days"]
        start = __import__("datetime").date.today()
        dates = [(start + __import__("datetime").timedelta(days=i)).isoformat()
                 for i in range(days)]
        return Response({"daily": {"time": dates,
                                   "temperature_2m_max": [25] * days,
                                   "temperature_2m_min": [20] * days,
                                   "weather_code": [3] * days,
                                   "precipitation_probability_max": [0] * days}})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = weather.OpenMeteoWeather().fetch(city="澳门", days=days)
    assert result.status.value == "ok"
    assert len(result.data["预报"]) == days


def test_llm_speculative_date_does_not_drop_first_day():
    today = nodes._dt.date.today()
    merged = {
        "days": 3,
        # 模拟 LLM 把“3天”错误填成明天开始，但用户没有说“明天”。
        "date": (today + nodes._dt.timedelta(days=1)).isoformat(),
        "start_date": (today + nodes._dt.timedelta(days=1)).isoformat(),
    }
    assert nodes._apply_days_date_range(merged, {}, 3)
    assert merged["start_date"] == today.isoformat()
    assert merged["end_date"] == (today + nodes._dt.timedelta(days=2)).isoformat()


def test_explicit_date_still_anchors_n_day_range():
    merged = {"days": 3}
    assert nodes._apply_days_date_range(
        merged, {}, 3, explicit_date="2026-11-01")
    assert merged["start_date"] == "2026-11-01"
    assert merged["end_date"] == "2026-11-03"
