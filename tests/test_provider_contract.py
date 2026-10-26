from app.tools.registry import tool_source_id
from app.tools.showapi import ShowAPIAirQuality


def test_entity_tool_source_ids_do_not_collide():
    first = tool_source_id("amap-poi", "2026-10-25T12:00:00", "大三巴")
    second = tool_source_id("amap-poi", "2026-10-25T12:00:00", "大运河")
    assert first != second


def test_showapi_marks_unsupported_area(monkeypatch):
    monkeypatch.setenv("SHOWAPI_APP_KEY", "test")
    result = ShowAPIAirQuality().fetch(city="澳门")
    assert result.status.value == "unavailable"
    assert result.error_code == "city_not_supported"
