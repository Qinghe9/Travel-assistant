from app.agent.nodes import _answer_completion_issues


def test_itinerary_period_and_overlap_validation():
    state = {"intent": "INT-02", "intents": ["INT-02"], "slots": {"days": 1}, "tasks": []}
    text = "第一天\n上午：09:00-11:00 景点A\n下午：10:00-12:00 景点B"
    issues = _answer_completion_issues(state, text)
    assert "第一天存在时间重叠" in issues


def test_itinerary_missing_period_is_detected():
    state = {"intent": "INT-02", "intents": ["INT-02"], "slots": {"days": 1}, "tasks": []}
    issues = _answer_completion_issues(state, "第一天\n景点A。")
    assert "第一天缺少上午/下午/晚间时段" in issues
