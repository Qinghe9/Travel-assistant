from app.agent import nodes
from app.tools import amap, registry
from app.models.schemas import Intent
import pytest


def test_amap_poi_receives_county_as_city(monkeypatch):
    captured = {}

    def fake_get(path, params, timeout=4.0):
        captured.update({"path": path, "params": params})
        return {"pois": [{
            "name": "婺源篁岭", "adname": "婺源县", "address": "江湾镇",
            "location": "117.95,29.29",
        }]}

    monkeypatch.setattr(amap, "_get", fake_get)
    result = amap.AmapPOI().fetch(city="婺源")
    assert result.status.value == "ok"
    assert captured["params"]["city"] == "婺源"
    assert captured["params"]["citylimit"] == "true"


def test_call_tools_passes_resolved_county_and_traces_source(monkeypatch):
    captured = {}

    monkeypatch.setattr(nodes.geocode, "resolve_place", lambda name, trace=None: {
        "name": "婺源", "province": "江西", "city": "上饶", "county": "婺源",
        "adcode": "361130", "lat": 29.25, "lon": 117.86, "source": "amap",
    })

    def fake_run(intents, slots):
        captured["intents"] = intents
        captured["slots"] = slots
        return []

    monkeypatch.setattr(registry, "run_for_intents", fake_run)
    monkeypatch.setattr(registry, "to_evidence", lambda results: [])
    monkeypatch.setattr(registry, "unavailable_notes", lambda results: [])
    state = {
        "intent": Intent.DESTINATION.value,
        "intents": [Intent.DESTINATION.value],
        "slots": {"destination": "婺源县"},
        "query": "婺源县有什么好玩的",
    }
    result = nodes.call_tools(state)
    assert captured["slots"]["destination"] == "婺源"
    assert captured["slots"]["province"] == "江西"
    geocode_trace = result["trace"][0]["extra"]["geocode"]
    assert geocode_trace["source"] == "amap"
    assert geocode_trace["adcode"] == "361130"


@pytest.mark.parametrize("query,expected", [
    ("婺源有什么好玩的", "婺源"),
    ("凯里明天天气", "凯里"),
    ("敦煌3天行程", "敦煌"),
    ("帮我规划4天西安行程", "西安"),
])
def test_loose_query_extracts_place_candidate(query, expected):
    assert nodes._place_candidate_from_query(query) == expected
