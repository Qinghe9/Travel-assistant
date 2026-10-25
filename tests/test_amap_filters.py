from app.tools import amap, showapi


def test_food_filters_lifestyle_and_keeps_restaurant(monkeypatch):
    captured = {}

    def fake_get(path, params, timeout=4.0):
        captured.update(params)
        return {"pois": [
            {"name": "茉里美甲美睫", "type": "餐饮服务",
             "adname": "婺源县", "address": "某路1号"},
            {"name": "婺源农家菜", "type": "餐饮服务",
             "adname": "婺源县", "address": "某路2号"},
        ]}

    monkeypatch.setattr(amap, "api_key", lambda: "test-key")
    monkeypatch.setattr(amap, "geocode", lambda center, city=None: {
        "location": "117.86,29.25", "adcode": "361130",
    })
    monkeypatch.setattr(amap, "_get", fake_get)
    result = amap.AmapFoodPOI().fetch(city="婺源")
    assert result.status.value == "ok"
    assert captured["types"] == "050000"
    assert result.data["餐饮"] == ["婺源农家菜（婺源县，某路2号）"]


def test_near_duplicate_scenic_pois_are_merged(monkeypatch):
    def fake_get(path, params, timeout=4.0):
        return {"pois": [
            {"name": "鸣沙山月牙泉", "adname": "敦煌市",
             "address": "鸣山南路", "location": "94.6800,40.0900"},
            {"name": "鸣沙山月牙泉风景名胜区", "adname": "敦煌市",
             "address": "鸣山南路景区内", "location": "94.6801,40.0901"},
        ]}

    monkeypatch.setattr(amap, "_get", fake_get)
    result = amap.AmapPOI().fetch(city="敦煌")
    assert result.status.value == "ok"
    assert len(result.data["景点"]) == 1
    assert "风景名胜区" in result.data["景点"][0]
    assert "景区内" in result.data["景点"][0]
    assert len(result.data["_points"]) == 1


def test_county_air_quality_marks_prefecture_fallback(monkeypatch):
    monkeypatch.setattr(showapi, "app_key", lambda: "test-key")
    monkeypatch.setattr(showapi, "_call", lambda path, form, timeout=5.0: {
        "area": "酒泉", "quality": "良好", "aqi": 52,
        "pm2_5": 20, "pm10": 54, "primary_pollutant": "PM10",
        "ct": "2026-10-25T12:00:00",
    })
    result = showapi.ShowAPIAirQuality().fetch(city="敦煌")
    assert result.status.value == "ok"
    assert result.data["城市"] == "酒泉"
    assert result.data["监测区域"] == "酒泉"
    assert "敦煌上级行政区，非本地监测点" in result.data["区域说明"]
