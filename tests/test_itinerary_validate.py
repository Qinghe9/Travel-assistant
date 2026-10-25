from app.agent.nodes import _answer_completion_issues, _coalesce_same_attraction_periods


def test_itinerary_period_and_overlap_validation():
    state = {"intent": "INT-02", "intents": ["INT-02"], "slots": {"days": 1}, "tasks": []}
    text = "第一天\n上午：09:00-11:00 景点A\n下午：10:00-12:00 景点B"
    issues = _answer_completion_issues(state, text)
    assert "第一天存在时间重叠" in issues


def test_itinerary_missing_period_is_detected():
    state = {"intent": "INT-02", "intents": ["INT-02"], "slots": {"days": 1}, "tasks": []}
    issues = _answer_completion_issues(state, "第一天\n景点A。")
    assert "第一天缺少上午/下午/晚间时段" in issues


def test_same_attraction_is_not_split_into_two_periods():
    text = (
        "- 上午：鸣沙山月牙泉风景名胜区（敦煌市）[1]。\n"
        "- 下午：同景区继续游览，建议时长待确认 [1]。"
    )
    merged = _coalesce_same_attraction_periods(text)
    assert "上午至下午：鸣沙山月牙泉风景名胜区" in merged
    assert "下午：同景区继续" not in merged
    assert merged.count("鸣沙山月牙泉") == 1


def test_same_attraction_phrase_variant_is_merged():
    text = (
        "- 上午：鸣沙山月牙泉风景名胜区（敦煌市）[1]。\n"
        "- 下午：同一景区不重复安排，可在景区内继续游览 [1]。"
    )
    merged = _coalesce_same_attraction_periods(text)
    assert "上午至下午：鸣沙山月牙泉风景名胜区" in merged
    assert "下午：同一景区" not in merged
