from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_HTML = (ROOT / "app" / "api" / "templates" / "demo.html").read_text(
    encoding="utf-8")
DEMO_CSS = (ROOT / "app" / "api" / "static" / "styles.css").read_text(
    encoding="utf-8")
DEMO_JS = (ROOT / "app" / "api" / "static" / "demo.js").read_text(
    encoding="utf-8")


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


def test_frontend_is_split_into_static_assets():
    assert '<link rel="stylesheet" href="/static/styles.css?v=' in DEMO_HTML
    assert '<script src="/static/demo.js?v=' in DEMO_HTML
    assert '<script src="/static/markdown.js?v=' in DEMO_HTML
    assert "<style>" not in DEMO_HTML
    assert "function send()" not in DEMO_HTML
    assert ":root{" in DEMO_CSS
    assert "async function send()" in DEMO_JS
