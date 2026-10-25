from app.agent import nodes
from app.agent import slots


def test_province_small_cities_in_rule_fallback():
    for city in ("安顺", "遵义", "凯里", "都匀", "赤水", "兴义"):
        assert slots.detect_city("%s旅游" % city) == city


def test_placeholder_lines_are_not_counted_as_unbound():
    assert nodes._is_placeholder_line("- 建议时长：待确认。")
    assert nodes._is_placeholder_line("交通衔接：具体线路待确认")
    assert not nodes._is_placeholder_line("- 上午：游览黔灵山 [1]。")


def test_province_default_notice():
    text = nodes._destination_source_notice(
        {"destination_source": "province_default", "slots": {"destination": "贵阳"}})
    assert "按省会贵阳规划" in text
