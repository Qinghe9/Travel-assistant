#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把工程代码拆成两份适合 AI 审查的 Markdown 快照。

Part 1：Agent、API、数据模型、外部工具和运行配置。
Part 2：RAG、脚本、测试、评测、CI 和项目文档。

复用 export_code_snapshot.py 的文件筛选、密钥扫描和代码围栏逻辑。

用法：
    python scripts/export_code_snapshot_split.py
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))

from scripts import export_code_snapshot as exporter

ROOT = exporter.ROOT
DOCS = ROOT / "docs"
PART1 = DOCS / "PROJECT_CODE_PART1_AGENT_API.md"
PART2 = DOCS / "PROJECT_CODE_PART2_RAG_SCRIPTS.md"

PART1_PREFIXES = ("app/agent/", "app/api/", "app/models/", "app/tools/")
PART1_ROOT_FILES = {
    ".env.example", ".gitignore", "requirements.txt", "README.md",
    "app/__init__.py",
}


def git(*args: str) -> str:
    return exporter.git(*args)


def select_files(output_relatives: set[str]) -> tuple[list, list]:
    selected, skipped = [], []
    for relative in exporter.git_paths():
        if any(not exporter.should_include(relative, output) for output in output_relatives):
            skipped.append(relative)
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        try:
            text = exporter.read_text(path)
        except (UnicodeDecodeError, OSError):
            skipped.append(relative)
            continue
        if any(pattern.search(text) for pattern in exporter.SECRET_PATTERNS):
            skipped.append(relative + "（疑似密钥，已排除）")
            continue
        selected.append((relative, text))
    return selected, skipped


def part_for(relative: str) -> int:
    if relative in PART1_ROOT_FILES or relative.startswith(PART1_PREFIXES):
        return 1
    return 2


def render(
    output: Path,
    part_no: int,
    files: list,
    skipped: list,
    branch: str,
    commit: str,
    status: str,
    generated: str,
) -> None:
    if part_no == 1:
        title = "Travel-Agent 工程代码快照（1/2）：Agent 与 API 运行时"
        other = PART2.name
        scope = (
            "本份包含 LangGraph Agent、FastAPI/SSE、状态模型、外部工具、运行配置和根 README。"
        )
    else:
        title = "Travel-Agent 工程代码快照（2/2）：RAG、脚本、测试与 CI"
        other = PART1.name
        scope = (
            "本份包含 RAG 索引/检索/生成、运行与测试脚本、评测代码、CI、NOTICE 和 PRD 对照。"
        )

    total_lines = sum(len(text.splitlines()) for _, text in files)
    out = [
        "# " + title,
        "",
        "> 本文件由 `scripts/export_code_snapshot_split.py` 自动生成。",
        f"> 这是两部分中的第 {part_no} 份，配套文件：`{other}`。",
        "",
        "## 快照信息",
        "",
        f"- 生成时间：`{generated}`",
        f"- Git 分支：`{branch}`",
        f"- Git 提交：`{commit}`",
        f"- 本份范围：{scope}",
        f"- 本份文件数：`{len(files)}`",
        f"- 本份代码行数（约）：`{total_lines}`",
        "- 安全排除：`.env`、数据库、索引、图片、二进制、缓存、评测原始 JSON/JSONL 数据。",
        "",
        "## 给审查 AI 的说明",
        "",
        "- 请将本文件与另一份快照一起阅读；不要因为当前文件缺少某个模块就判断项目缺失。",
        "- Agent 主链路同时依赖本份的 `app/agent/` 和另一份的 `app/rag/`。",
        "- 不得假设被排除的数据或密钥存在；不要生成或索取真实密钥。",
        "",
        "## 建议直接发送给审查 AI 的提示",
        "",
        "```text",
        "我正在提供同一个工程的两份代码快照，请合并理解后审查。",
        "按 P0/P1/P2 输出问题，重点检查正确性、跨轮槽位污染、意图路由、",
        "工具失败恢复、日期/省份语义、RAG 引用绑定、任务覆盖、并发/性能和安全边界。",
        "每个问题必须给出文件路径、函数上下文、触发条件、根因、修复方案和回归测试。",
        "不要只做摘要，不要假设未包含的数据或密钥存在，不要编造代码。",
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

    for index, (relative, text) in enumerate(files, start=1):
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        out.append(
            f"{index}. `{relative}` — {len(text.splitlines())} 行 — sha256:{digest}"
        )

    out.extend(["", "## 完整文件内容", ""])
    for relative, text in files:
        fence = exporter.code_fence(text)
        out.extend([
            f"<!-- BEGIN_FILE: {relative} -->",
            f"### `{relative}`",
            "",
            f"{fence}{exporter.language_for(relative)}",
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
    print("已生成：%s（%d 文件，%.1f KB）" % (
        output, len(files), output.stat().st_size / 1024))


def main() -> int:
    output_relatives = {
        PART1.resolve().relative_to(ROOT.resolve()).as_posix(),
        PART2.resolve().relative_to(ROOT.resolve()).as_posix(),
    }
    selected, skipped = select_files(output_relatives)
    part1 = [item for item in selected if part_for(item[0]) == 1]
    part2 = [item for item in selected if part_for(item[0]) == 2]

    branch = git("branch", "--show-current") or "(detached)"
    commit = git("rev-parse", "HEAD")
    status = git("status", "--short")
    generated = datetime.now().astimezone().isoformat(timespec="seconds")

    render(PART1, 1, part1, skipped, branch, commit, status, generated)
    render(PART2, 2, part2, skipped, branch, commit, status, generated)
    print("合计：%d 文件，%d 行" % (
        len(selected), sum(len(text.splitlines()) for _, text in selected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
