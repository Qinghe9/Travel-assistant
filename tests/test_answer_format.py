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
