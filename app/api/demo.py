# -*- coding: utf-8 -*-
"""前端页面路由；页面、样式和脚本分别放在 templates/ 与 static/。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()
API_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = API_DIR / "templates"


def _page(name: str) -> HTMLResponse:
    path = TEMPLATE_DIR / name
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate",
                 "Pragma": "no-cache"},
    )


@router.get("/", response_class=HTMLResponse)
def index():
    return _page("index.html")


@router.get("/demo", response_class=HTMLResponse)
def demo():
    return _page("demo.html")
