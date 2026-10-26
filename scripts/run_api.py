#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动 Agent API（默认 8001，对应 PRD 的 api 服务端口）。

用法：
    python scripts/run_api.py
    python scripts/run_api.py --port 8001 --reload
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8001)
ap.add_argument("--reload", action="store_true")
a = ap.parse_args()

import uvicorn  # noqa: E402

uvicorn.run("app.api.main:app", host=a.host, port=a.port, reload=a.reload)
