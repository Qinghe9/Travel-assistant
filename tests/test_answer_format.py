from app.tools.showapi import ShowAPIHoliday

from app.agent import nodes, prompts
from app.models.schemas import Intent
from app.rag.generation.answer import post_validate


SID = "abcdef123456"
CITATIONS = [{"source_id": SID, "index": 1, "kind": "tool", "provider": "test"}]


def test_itinerary_prompt_contains_visual_skeleton():
    rule = prompts.answer_format_rule(
        [Intent.ITINERARY, Intent.WEATHER], days=5)
    assert "# 一、5天行程" in rule
    assert "## 第 1 天｜主题" in rule
    assert "**上午**" in rule
    assert "> 交通衔接" in rule
    assert "缺天级标题会被判为不完整并重写" in rule


def test_structural_lines_do_not_require_citations():
    text = (
        "# 一、五天行程\n"
        "## 第 1 天｜老城文化线\n"
        "- **上午**：陈家祠堂 [%s]\n"
        "> 交通衔接：证据未提供"
    ) % SID
    kept, dropped, unbound = post_validate(text, CITATIONS)
    assert "# 一、五天行程" in kept
    assert "## 第 1 天｜老城文化线" in kept
    assert kept.count("> 交通衔接") == 1
    assert dropped == []
    assert unbound == []
    assert nodes._is_structural_line("# 一、五天行程")
    assert nodes._is_structural_line("> 说明")
    assert not nodes._is_structural_line("- **上午**：陈家祠堂")


def test_bold_period_does_not_break_citation_numbering():
    numbered, used = nodes._number_citations(
        "- **上午**：陈家祠堂 [%s]" % SID, CITATIONS)
    assert "**上午**" in numbered
    assert "[1]" in numbered
    assert used[0]["source_id"] == SID


def test_completion_check_accepts_new_day_heading_format():
    state = {"intent": "INT-02", "intents": ["INT-02"], "slots": {"days": 2}, "tasks": []}
    text = (
        "# 一、2天行程\n"
        "## 第 1 天｜老城\n- **上午**：景点A\n- **下午**：景点B\n"
        "## 第 2 天｜自然\n- **上午**：景点C\n- **下午**：景点D"
    )
    assert nodes._answer_completion_issues(state, text) == []


def test_day_headings_inserted_only_with_reliable_count():
    state = {
        "slots": {"days": 2, "start_date": "2026-10-26", "end_date": "2026-10-27"},
        "tool_results": [],
    }
    text = (
        "- **上午**：景点A\n- **下午**：景点B\n"
        "- **上午**：景点C\n- **下午**：景点D"
    )
    result = nodes._insert_itinerary_day_headings(text, state)
    assert "## 第 1 天｜待补充主题" in result
    assert "## 第 2 天｜待补充主题" in result


def test_day_headings_not_inserted_when_count_is_unreliable():
    state = {"slots": {"days": 3}, "tool_results": []}
    text = "- **上午**：景点A\n- **下午**：景点B"
    assert nodes._insert_itinerary_day_headings(text, state) == text


def test_placeholder_fields_removed_but_real_transport_kept():
    text = (
        "- **上午**：西安城墙（步行约11.4km，约3-3.5小时）\n"
        "> 交通衔接：城墙步行全长约11.4km，全程约3-3.5小时\n"
        "- **晚间**：证据未提供晚间可安排的具体景点\n"
        "- **上午**：凯里市区自由活动（证据未提供更多可排入景点）\n"
        "- **上午至下午**：凯里市区自由活动（证据未提供更多可排入景点）\n"
        "- **上午至下午**：凯里市区自由活动，可围绕已列景点周边安排休闲游览 [1]\n"
        "- 交通衔接：证据未提供各景点间交通方式与耗时"
    )
    cleaned, removed = nodes._remove_field_placeholders(text)
    assert removed == 5
    assert "城墙步行全长约11.4km" in cleaned
    assert "证据未提供" not in cleaned


def test_paused_poi_is_filtered_and_disclosed():
    evidence = [{
        "source_id": SID,
        "title": "凯里景点",
        "kind": "amap-poi",
        "text": "云谷田园目前暂停开放。云谷田园位于凯里郊区。下司古镇正常开放。",
    }]
    filtered, paused = nodes._filter_paused_evidence(evidence)
    assert "云谷田园" in paused
    assert "云谷田园" not in filtered[0]["text"]
    assert "下司古镇" in filtered[0]["text"]

    cleaned, removed = nodes._strip_paused_itinerary_lines(
        "- **上午**：云谷田园目前暂停开放，不宜安排\n- **下午**：下司古镇",
        paused,
    )
    assert removed == 1
    assert "云谷田园" not in cleaned
    assert "下司古镇" in cleaned


def test_paused_poi_is_removed_from_tool_lists_and_points():
    result = {
        "provider": "amap-poi",
        "fetched_at": "2026-10-26T12:00:00",
        "data": {
            "景点": ["下司古镇（凯里市）", "云谷田园(暂停开放)（凯里市）"],
            "_points": [
                {"name": "下司古镇", "location": "1,2"},
                {"name": "云谷田园", "location": "3,4"},
            ],
            "_source_hint": "凯里",
        },
    }
    paused = nodes._filter_paused_tool_results([result])
    assert "云谷田园" in paused
    assert result["data"]["景点"] == ["下司古镇（凯里市）"]
    assert [point["name"] for point in result["data"]["_points"]] == ["下司古镇"]


def test_holiday_result_does_not_leak_internal_boolean(monkeypatch):
    import app.tools.showapi as showapi

    monkeypatch.setattr(showapi, "app_key", lambda: "test")
    monkeypatch.setattr(showapi, "_call", lambda *args, **kwargs: {
        "cn": "周一", "holiday": "无", "type": "0",
        "begin": "", "end": "", "holiday_remark": "",
    })
    result = ShowAPIHoliday().fetch(date="2026-10-26")
    assert result.status.value == "ok"
    assert result.data["日期性质"] == "非节假日"
    assert result.data["日期说明"] == "2026-10-26为周一，非节假日"
    assert "是否假日" not in result.data
