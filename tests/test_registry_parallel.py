import time

from app.agent.intents import Intent
from app.models.schemas import ToolResult, ToolStatus
from app.tools import registry


class SleepProvider:
    name = "sleep"

    def __init__(self, fail=False):
        self.fail = fail

    def fetch(self):
        return None

    def run(self, **kwargs):
        time.sleep(0.5)
        if self.fail:
            raise RuntimeError("boom")
        return ToolResult(provider=self.name, status=ToolStatus.OK)


def test_five_tools_run_in_parallel_and_exception_is_isolated(monkeypatch):
    monkeypatch.setattr(registry, "INTENT_TOOLS",
                        {Intent.ITINERARY: ("a", "b", "c", "d", "e")})
    monkeypatch.setattr(registry, "TOOL_REQUIREMENTS", {})
    monkeypatch.setattr(registry, "get_provider",
                        lambda name: SleepProvider(fail=name == "c"))
    started = time.perf_counter()
    results = registry.run_for_intents([Intent.ITINERARY], {})
    elapsed = time.perf_counter() - started
    assert elapsed < 1.5
    assert len(results) == 5
    assert sum(r.status is ToolStatus.OK for r in results) == 4
    assert sum(r.status is ToolStatus.UNAVAILABLE for r in results) == 1
