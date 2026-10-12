#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR 自动审查：把 PR 的 diff 交给 OpenAI 兼容接口（默认 DeepSeek），评审意见贴回 PR。

用法（由 .github/workflows/ai-review.yml 自动调用）：
    python .github/scripts/ai_review.py

环境变量：
    GITHUB_TOKEN      Actions 自动注入，用于拉 diff 和发评论
    REVIEW_API_KEY    模型密钥；未配置时直接跳过（退出码 0，不阻塞 PR）
    REVIEW_API_BASE   默认 https://api.deepseek.com/v1
    REVIEW_MODEL      默认 deepseek-chat
    REVIEW_MAX_DIFF   送入模型的 diff 字符上限，默认 60000
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

GITHUB_API = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
MARKER = "<!-- ai-review -->"

SYSTEM_PROMPT = """你是一位资深 Python / RAG 系统代码评审员，正在评审一个中文课程项目（旅游规划助手 RAG 模块）。
只针对本次 diff 改动的内容评审，重点关注：
1. 正确性与边界条件（空输入、编码、HTTP 异常、重试与超时）；
2. 检索链路的正确性（分块、去重、source_id 稳定、证据可追溯、指标口径）；
3. 安全（密钥泄漏、路径穿越、注入、未校验的输入）；
4. 可维护性（重复代码、命名、异常吞没、日志）。
输出 Markdown，按「🔴 必须修复」「🟡 建议」「🟢 亮点」三节组织，每条给出 `文件:行` 与具体修改建议。
没有问题就直说，不要为凑数编造，不要复述 diff，总长不超过 800 字。用中文回答。"""


def log(msg: str) -> None:
    print(msg, flush=True)


def http(method, url, token=None, data=None, accept="application/vnd.github+json", raw=False, timeout=120):
    """最小 HTTP 封装：只用标准库。raw=True 时返回文本（用于取 diff）。"""
    body = None
    headers = {"User-Agent": "ai-review-bot", "Accept": accept}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
    if raw:
        return payload.decode("utf-8", "replace")
    return json.loads(payload) if payload else {}


def main() -> int:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    api_key = (os.environ.get("REVIEW_API_KEY") or "").strip()
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    event_path = os.environ.get("GITHUB_EVENT_PATH") or ""

    if not token or not repo:
        log("缺少 GITHUB_TOKEN / GITHUB_REPOSITORY，跳过")
        return 0
    if not api_key:
        log("未配置 REVIEW_API_KEY（仓库 Secret: DEEPSEEK_API_KEY），跳过 AI 审查")
        return 0

    try:
        with open(event_path, "r", encoding="utf-8") as fh:
            event = json.load(fh)
    except OSError as exc:
        log("读取事件负载失败：%s" % exc)
        return 0

    pr = event.get("pull_request") or {}
    number = pr.get("number") or event.get("number")
    if not number:
        log("不是 PR 事件，跳过")
        return 0

    diff = http(
        "GET",
        "%s/repos/%s/pulls/%d" % (GITHUB_API, repo, number),
        token=token,
        accept="application/vnd.github.v3.diff",
        raw=True,
    )
    if not diff.strip():
        log("diff 为空，跳过")
        return 0

    limit = int(os.environ.get("REVIEW_MAX_DIFF") or 60000)
    truncated = len(diff) > limit
    user = "PR 标题：%s\nPR 描述：%s\n\n```diff\n%s\n```%s" % (
        pr.get("title", ""),
        (pr.get("body") or "（无）")[:2000],
        diff[:limit],
        "\n\n（注意：diff 过长已截断，只评审以上部分）" if truncated else "",
    )

    base = (os.environ.get("REVIEW_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("REVIEW_MODEL") or "deepseek-chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1600,
        "stream": False,
    }

    try:
        out = http(
            "POST", base + "/chat/completions", token=api_key, data=payload,
            accept="application/json", timeout=180,
        )
        review = (out["choices"][0]["message"]["content"] or "").strip()
    except urllib.error.HTTPError as exc:
        log("模型接口返回 %s：%s" % (exc.code, exc.read()[:500]))
        return 0
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as exc:
        log("调用模型失败：%s" % exc)
        return 0

    if not review:
        log("模型返回空内容，跳过")
        return 0

    log(review)
    comment = (
        "%s\n## 🤖 自动代码审查（%s）\n\n%s\n\n---\n"
        "<sub>由 GitHub Actions 自动生成，仅供参考；调整规则见 <code>.github/workflows/ai-review.yml</code>。</sub>"
        % (MARKER, model, review)
    )
    try:
        http(
            "POST",
            "%s/repos/%s/issues/%d/comments" % (GITHUB_API, repo, number),
            token=token,
            data={"body": comment},
        )
        log("已把评审意见贴到 PR #%d" % number)
    except urllib.error.HTTPError as exc:
        log("发评论失败（%s）：%s" % (exc.code, exc.read()[:300]))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        try:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write("## 自动代码审查\n\n" + review + "\n")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
