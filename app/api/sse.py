# -*- coding: utf-8 -*-
"""SSE 帧编码。"""

from __future__ import annotations

import json


def sse_frame(payload: dict) -> str:
    """一条 SSE 帧必须以空行结束。"""
    return "data: %s\n\n" % json.dumps(payload, ensure_ascii=False, default=str)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}
