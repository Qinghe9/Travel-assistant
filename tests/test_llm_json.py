from pydantic import RootModel

from app.agent import llm
from app.models.schemas import LLMIntentPayload


class StringList(RootModel[list[str]]):
    pass


def test_chat_json_repairs_once(monkeypatch):
    calls = iter([
        {"text": "不是 JSON"},
        {"text": '{"intent":"INT-02","confidence":0.9,"slots":{"days":3}}'},
    ])
    monkeypatch.setattr(llm._rag_llm, "chat", lambda *a, **k: next(calls))
    obj, err = llm.chat_json([], LLMIntentPayload)
    assert err == ""
    assert obj.intent == "INT-02"


def test_chat_json_returns_error_after_second_failure(monkeypatch):
    monkeypatch.setattr(llm._rag_llm, "chat", lambda *a, **k: {"text": "still bad"})
    obj, err = llm.chat_json([], LLMIntentPayload)
    assert obj is None
    assert "自动修复调用" in err or "不是合法 JSON" in err


def test_chat_json_accepts_array_root_schema(monkeypatch):
    monkeypatch.setattr(
        llm._rag_llm, "chat",
        lambda *a, **k: {"text": '说明文字 ["北京", "上海"] 结束'},
    )
    obj, err = llm.chat_json([], StringList)
    assert err == ""
    assert obj.root == ["北京", "上海"]
