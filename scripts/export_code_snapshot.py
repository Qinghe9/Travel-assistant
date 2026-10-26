#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把项目源码、配置、测试与文档导出为单个 Markdown 快照。

设计目标：
- 其他 AI 只需读取一个文件即可理解工程；
- 排除 .env、数据库、索引、图片、缓存和评测原始数据；
- 每个文件带路径、行数、SHA-256，便于校验是否过期；
- 自动选择代码围栏长度，避免文件内的 ``` 破坏 Markdown。

用法：
    python scripts/export_code_snapshot.py
    python scripts/export_code_snapshot.py --output docs/PROJECT_CODE_SNAPSHOT.md
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "PROJECT_CODE_SNAPSHOT.md"

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".ini", ".cfg",
    ".example", ".sh", ".ps1",
}
TEXT_NAMES = {".gitignore", "NOTICE", "Dockerfile", "Procfile"}
BINARY_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".7z", ".rar", ".db", ".sqlite", ".sqlite3", ".pyc", ".pt", ".pth",
    ".bin", ".onnx", ".parquet", ".arrow", ".npy", ".npz",
}
EXCLUDED_PREFIXES = (
    ".git/", "data/", "__pycache__/", ".venv/", "venv/", "node_modules/",
)
EXCLUDED_NAMES = {".env"}
EVAL_DATA_PATTERNS = (
    re.compile(r"^evals/.*\.(?:json|jsonl)$"),
)
LANG_BY_SUFFIX = {
    ".py": "python", ".md": "markdown", ".txt": "text", ".yml": "yaml",
    ".yaml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini",
    ".sh": "bash", ".ps1": "powershell", ".example": "dotenv",
}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return result.stdout.strip()


def git_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, check=False,
    )
    return sorted(
        item.decode("utf-8", "replace")
        for item in result.stdout.split(b"\0") if item
    )


def should_include(relative: str, output_relative: str) -> bool:
    path = Path(relative)
    if relative == output_relative:
        return False
    if relative in EXCLUDED_NAMES or relative.startswith(EXCLUDED_PREFIXES):
        return False
    if path.name in EXCLUDED_NAMES:
        return False
    if path.suffix.lower() in BINARY_SUFFIXES:
        return False
    if any(pattern.match(relative) for pattern in EVAL_DATA_PATTERNS):
        return False
    return path.suffix.lower() in TEXT_SUFFIXES or path.name in TEXT_NAMES


def read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\0" in data:
        raise UnicodeDecodeError("utf-8", data, 0, 1, "binary content")
    return data.decode("utf-8")


def code_fence(text: str) -> str:
    runs = [len(match.group(0)) for match in re.finditer(r"`+", text)]
    return "`" * max(3, (max(runs) + 1) if runs else 3)


def language_for(relative: str) -> str:
    path = Path(relative)
    if path.name == ".env.example":
        return "dotenv"
    return LANG_BY_SUFFIX.get(path.suffix.lower(), "text")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    output_relative = output.resolve().relative_to(ROOT.resolve()).as_posix()

    selected, skipped = [], []
    for relative in git_paths():
        if not should_include(relative, output_relative):
            skipped.append(relative)
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = read_text(path)
        except (UnicodeDecodeError, OSError):
            skipped.append(relative)
            continue
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            skipped.append(relative + "（疑似密钥，已排除）")
            continue
        selected.append((relative, text))

    branch = git("branch", "--show-current") or "(detached)"
    commit = git("rev-parse", "HEAD")
    status = git("status", "--short")
    generated = datetime.now().astimezone().isoformat(timespec="seconds")
    total_lines = sum(text.count("\n") + 1 for _, text in selected)

    out = [
        "# Travel-Agent 工程代码单文件快照",
        "",
        "> 本文件由 `scripts/export_code_snapshot.py` 自动生成，供其他 AI 做代码审查、纠错和优化。",
        "",
        "## 快照信息",
        "",
        f"- 生成时间：`{generated}`",
        f"- Git 分支：`{branch}`",
        f"- Git 提交：`{commit}`",
        f"- 文件数：`{len(selected)}`",
        f"- 代码总行数（约）：`{total_lines}`",
        "- 安全排除：`.env`、数据库、索引、图片、二进制、缓存、评测原始 JSON/JSONL 数据。",
        "",
        "## 给审查 AI 的上下文",
        "",
        "- 项目是 FastAPI + LangGraph 的旅游规划 Agent，并与 RAG 检索、外部工具组合。",
        "- Agent 主入口：`app/agent/graph.py`、`app/agent/nodes.py`、`app/agent/state.py`。",
        "- API 入口：`app/api/main.py`、`app/api/routes.py`。",
        "- RAG 入口：`app/rag/generation/answer.py`、`app/rag/retrieval/recall.py`。",
        "- 外部工具：`app/tools/registry.py` 及 `app/tools/` 下适配器。",
        "- 离线验收：`scripts/smoke_agent_offline.py`、`scripts/smoke_api_offline.py`。",
        "- 请优先检查未定义变量、跨轮状态污染、工具失败恢复、引用绑定、日期语义和性能回归。",
        "- 不得假设被排除的数据或密钥存在；不要生成或索取真实密钥。",
        "",
        "## 建议直接发送给审查 AI 的提示",
        "",
        "```text",
        "请完整阅读这份单文件工程快照，按 P0/P1/P2 输出代码审查报告。",
        "重点检查：正确性、跨轮状态污染、意图/槽位继承、工具失败恢复、",
        "日期与省份语义、证据引用、任务覆盖、并发与性能、安全边界。",
        "每个问题必须给出文件路径、函数/行号上下文、触发条件、根因和可执行修复建议。",
        "不要泛泛总结；不要假设被排除的数据或密钥存在；不要编造未出现的代码。",
        "最后列出建议补充的回归测试和最小修改顺序。",
        "```",
        "",
        "## 当前工作区状态",
        "",
        "```text",
        status or "(clean)",
        "```",
        "",
        "## 文件索引",
        "",
    ]
    for index, (relative, text) in enumerate(selected, start=1):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        out.append(
            f"{index}. `{relative}` — {text.count(chr(10)) + 1} 行 — sha256:{digest}"
        )
    out.extend([
        "",
        "## 完整文件内容",
        "",
    ])

    for relative, text in selected:
        fence = code_fence(text)
        out.extend([
            f"<!-- BEGIN_FILE: {relative} -->",
            f"### `{relative}`",
            "",
            f"{fence}{language_for(relative)}",
            text.rstrip("\n"),
            fence,
            "",
            f"<!-- END_FILE: {relative} -->",
            "",
        ])

    out.extend([
        "## 导出排除清单",
        "",
        "```text",
        "\n".join(skipped) if skipped else "(无)",
        "```",
        "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(out), encoding="utf-8")
    print("已生成：%s" % output)
    print("文件数：%d；总行数：%d；大小：%.1f KB" % (
        len(selected), total_lines, output.stat().st_size / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
