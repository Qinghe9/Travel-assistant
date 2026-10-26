import json

from app.agent import slots
from app.rag import config as rag_cfg


def test_county_scope_is_loaded_by_rule_fallback(tmp_path, monkeypatch):
    city_scope = tmp_path / "scope_cities.json"
    county_scope = tmp_path / "scope_counties.json"
    city_scope.write_text(json.dumps({"cities": []}), encoding="utf-8")
    county_scope.write_text(json.dumps({"counties": [
        {"name_zh": "婺源", "province": "江西", "city": "上饶",
         "county": "婺源", "lat": 29.25, "lon": 117.86},
        {"name_zh": "平潭", "province": "福建", "city": "福州",
         "county": "平潭", "lat": 25.5, "lon": 119.8},
    ]}), encoding="utf-8")
    monkeypatch.setattr(rag_cfg, "SCOPE_FILE", city_scope)
    monkeypatch.setattr(rag_cfg, "SCOPE_COUNTIES_FILE", county_scope)
    monkeypatch.setattr(slots, "_CITY_CACHE", None)
    assert slots.detect_city("婺源有什么好玩的") == "婺源"
    assert slots.detect_city("平潭明天天气") == "平潭"


def test_unknown_county_does_not_get_invented(monkeypatch):
    monkeypatch.setattr(slots, "_CITY_CACHE", ["北京", "上海"])
    assert slots.detect_city("某个不存在的小城有什么好玩的") is None
