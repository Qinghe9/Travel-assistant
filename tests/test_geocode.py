import json

import pytest

from app.agent import geocode
from app.tools import amap


def _write(path, key, rows):
    path.write_text(json.dumps({key: rows}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_geocode(tmp_path, monkeypatch):
    city_scope = tmp_path / "scope_cities.json"
    county_scope = tmp_path / "scope_counties.json"
    monkeypatch.setattr(geocode.rag_cfg, "SCOPE_FILE", city_scope)
    monkeypatch.setattr(geocode.rag_cfg, "SCOPE_COUNTIES_FILE", county_scope)
    monkeypatch.setattr(geocode, "CACHE_FILE", tmp_path / "geocode_cache.sqlite")
    monkeypatch.setattr(amap, "api_key", lambda: "")
    geocode.reset_caches()
    yield city_scope, county_scope
    geocode.reset_caches()


def test_scope_city_and_prefecture_city(isolated_geocode):
    city_scope, _ = isolated_geocode
    _write(city_scope, "cities", [
        {"name_zh": "贵阳", "province": "贵州", "lat": 26.64, "lon": 106.63},
        {"name_zh": "安顺", "province": "贵州", "lat": 26.25, "lon": 105.95},
    ])
    guiyang = geocode.resolve_place("贵阳")
    anshun = geocode.resolve_place("安顺市")
    assert guiyang["source"] == "scope"
    assert guiyang["city"] == "贵阳"
    assert anshun["name"] == "安顺"
    assert anshun["lat"] == 26.25


def test_geonames_county_fallback_without_amap(isolated_geocode, monkeypatch):
    class FakeIndex:
        alias2canon = {}
        name2rec = {
            "婺源": {"level": "county", "prov": "江西", "city": "上饶",
                     "county": "婺源", "lat": 29.25, "lon": 117.86},
        }

    monkeypatch.setattr(geocode, "_geo_index", lambda: FakeIndex())
    trace = []
    result = geocode.resolve_place("婺源县", trace=trace)
    assert result["source"] == "geonames"
    assert result["city"] == "上饶"
    assert result["county"] == "婺源"
    assert result["lat"] == 29.25
    assert any("geonames" in item for item in trace)


def test_unknown_place_returns_none(isolated_geocode, monkeypatch):
    monkeypatch.setattr(geocode, "_geo_index", lambda: False)
    assert geocode.resolve_place("不存在的地名") is None
    assert geocode.resolve_place("") is None


def test_amap_result_is_written_to_sqlite_cache(isolated_geocode, monkeypatch):
    calls = []

    def fake_geocode(name):
        calls.append(name)
        return {
            "location": "117.86,29.25", "adcode": "361130",
            "province": "江西省", "city": "上饶市", "district": "婺源县",
        }

    monkeypatch.setattr(amap, "api_key", lambda: "test-key")
    monkeypatch.setattr(amap, "geocode", fake_geocode)
    first = geocode.resolve_place("婺源")
    second = geocode.resolve_place("婺源")
    assert first["source"] == second["source"] == "amap"
    assert first["adcode"] == "361130"
    assert len(calls) == 1
