import httpx

from app.tools import weather


class Response:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


def test_weather_days_without_date_returns_multi_day(monkeypatch):
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
    result = weather.OpenMeteoWeather().fetch(city="澳门", days=4)
    assert result.status.value == "ok"
    assert len(result.data["预报"]) == 4
