from app.api.demo import DEMO_HTML


def test_demo_header_is_simplified():
    assert "<h1>旅游对话助手</h1>" in DEMO_HTML
    assert "告诉我你想去哪里" in DEMO_HTML
    assert '<a href="/health">健康检查</a>' in DEMO_HTML
    assert "打开调试台" not in DEMO_HTML
    assert "Swagger 文档" not in DEMO_HTML
    assert 'id="prefsChip"' not in DEMO_HTML
    assert "setPrefs" not in DEMO_HTML
    assert "SSE 流式输出" not in DEMO_HTML
    assert 'class="hint"' not in DEMO_HTML
