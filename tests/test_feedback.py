import asyncio

from app.api import routes
from app.models.schemas import FeedbackRequest


def _body(response):
    async def collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)
    return asyncio.run(collect())


def test_feedback_sqlite_and_csv_export(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "FEEDBACK_DB", tmp_path / "feedback.db")
    monkeypatch.setattr(routes, "FEEDBACK_LOG", tmp_path / "feedback.jsonl")
    routes.feedback(FeedbackRequest(trace_id="trace-1", score=1, reason="ok"))
    response = routes.export_feedback()
    body = _body(response)
    assert "trace-1" in body
    assert "score,reason,created_at" in body
    assert response.media_type == "text/csv"
