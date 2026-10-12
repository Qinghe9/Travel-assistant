#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PR 自动审查：把 PR 的 diff 交给 OpenAI 兼容接口，评审意见贴回 PR。

用法（由 .github/workflows/ai-review.yml 自动调用）：
    python .github/scripts/ai_review.py

本地预演（推荐：推之前先看一眼会评论什么）：
    git diff main...HEAD > pr.diff
    python .github/scripts/ai_review.py --diff-file pr.diff --dry-run

环境变量：
    GITHUB_TOKEN      Actions 自动注入，用于拉 diff 和发评论
    REVIEW_API_KEY    模型密钥；未配置时直接跳过（退出码 0，不阻塞 PR）
    REVIEW_API_BASE   默认 https://api.deepseek.com/v1
    REVIEW_MODEL      默认 deepseek-chat
    REVIEW_MAX_DIFF   送入模型的 diff 字符上限，默认 60000
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

GITHUB_API = (os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")
MARKER = "<!-- ai-review -->"

SYSTEM_PROMPT = """你是一位资深 Python / RAG 系统代码评审员，正在评审一个中文课程项目（旅游规划助手 RAG 模块）。
只针对本次 diff 改动的内容评审，重点关注：
1. 正确性与边界条件（空输入、编码、HTTP 异常、重试与超时）；
2. 检索链路正确性（分块、去重、source_id 稳定、证据可追溯、指标口径与评测集同源）；
3. 生成链路正确性（引用必须能对到证据、不得引入无关知识、超时预算内必须降级而不是空答）；
4. 安全（密钥泄漏、把 .env 或 token 提交进仓库、路径穿越、注入、未校验输入）；
5. 可维护性（重复代码、命名、异常吞没、日志）。

本项目特有的硬约束，请重点核对：
- 答案里的每条事实句都必须绑定 [source_id]，且该 id 必须出现在 citations 中；
- 写入回答的事实不得超出注入的证据，无证据时应走拒答分支；
- 查询向量与建库向量必须同一模型，否则检索结果无意义；
- 检索 + 生成要落在端到端 2 秒预算内，超时必须有抽取式兜底。

输出 Markdown，按「🔴 必须修复」「🟡 建议」「🟢 亮点」三节组织，每条给出 文件:行 与具体修改建议。
没有问题就直说，不要为凑数编造，不要复述 diff，总长不超过 800 字。用中文回答。"""

SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "疑似 API Key（sk- 开头）"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "疑似 GitHub Token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "私钥内容"),
    (re.compile(r"(?i)\b(?:api[_-]?key|secret|passwd|password|token)\s*[:=]\s*[\x22\x27][^\x22\x27\s]{16,}[\x22\x27]"),
     "硬编码的密钥/口令"),
]


def log(msg):
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


def scan_secrets(diff):
    """在新增行里找密钥特征。

    返回 [(路径, 说明, 命中长度)] —— **故意不回显命中内容**：
    评论是公开的，把密钥片段贴上去等于二次泄漏。只报告位置和类型。
    """
    hits, path, lineno = [], "", 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            continue
        if line.startswith("+++"):
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith("-"):
            continue                       # 删除行不占新文件的行号
        if not line.startswith("+"):
            lineno += 1                    # 上下文行也占一行
            continue
        here = lineno                      # 本行在新文件里的行号
        lineno += 1
        for pat, label in SECRET_PATTERNS:
            m = pat.search(line[1:])
            if m:
                hits.append(("%s:%d" % (path, here), label, len(m.group(0))))
    return hits


def redact(text):
    """兜底脱敏：模型很可能在评审里把密钥原文抄一遍，贴到公开评论就成了二次泄漏。"""
    for pat, _ in SECRET_PATTERNS:
        text = pat.sub("[已隐藏疑似密钥]", text)
    return text


def changed_files(diff):
    files, add, dele = [], 0, 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            add += 1
        elif line.startswith("-") and not line.startswith("---"):
            dele += 1
    return files, add, dele


def upsert_comment(repo, number, token, body, dry_run=False):
    """同一条评论只保留一份：先找带 MARKER 的历史评论，找到就改，找不到才新建。"""
    if dry_run or not number:
        log("[dry-run] 本应写入/更新 PR 评论（前 1500 字）：")
        log(body[:1500])
        return "dry-run"
    try:
        existing = http("GET", "%s/repos/%s/issues/%d/comments?per_page=100" % (GITHUB_API, repo, number),
                        token=token)
        for c in existing:
            if MARKER in (c.get("body") or ""):
                http("PATCH", "%s/repos/%s/issues/comments/%d" % (GITHUB_API, repo, c["id"]),
                     token=token, data={"body": body})
                log("已更新既有评审评论 %s" % c.get("html_url", c["id"]))
                return "updated"
        http("POST", "%s/repos/%s/issues/%d/comments" % (GITHUB_API, repo, number),
             token=token, data={"body": body})
        log("已把评审意见贴到 PR #%d" % number)
        return "created"
    except urllib.error.HTTPError as exc:
        log("发评论失败（%s）：%s" % (exc.code, exc.read()[:300]))
        return "failed"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--diff-file", help="本地 diff 文件（不给则从 PR 事件里拉）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不发评论")
    a = ap.parse_args()

    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    api_key = (os.environ.get("REVIEW_API_KEY") or "").strip()
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    event_path = os.environ.get("GITHUB_EVENT_PATH") or ""

    pr, number, diff = {}, None, ""
    if a.diff_file:
        try:
            raw = open(a.diff_file, "rb").read()
        except OSError as exc:
            log("读取 diff 文件失败：%s" % exc)
            return 0
        # PowerShell 的 > 默认写 UTF-16LE，这里自动识别，免得解析出 0 个文件
        if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            diff = raw.decode("utf-16", "replace")
        elif b"\x00" in raw[:200]:
            diff = raw.decode("utf-16-le", "replace")
        else:
            diff = raw.decode("utf-8", "replace")
        log("本地预演模式（--diff-file），不会发评论")
        pr = {"title": (os.environ.get("PR_TITLE") or "（本地预演）"), "body": ""}
    else:
        if not token or not repo:
            log("缺少 GITHUB_TOKEN / GITHUB_REPOSITORY，跳过")
            return 0
        forced = (os.environ.get("PR_NUMBER") or "").strip()
        if forced.isdigit():
            # 手动触发（workflow_dispatch）时直接指定 PR 号
            number = int(forced)
            pr = http("GET", "%s/repos/%s/pulls/%d" % (GITHUB_API, repo, number), token=token)
        else:
            try:
                with open(event_path, "r", encoding="utf-8") as fh:
                    event = json.load(fh)
            except OSError as exc:
                log("读取事件负载失败：%s" % exc)
                return 0
            pr = event.get("pull_request") or {}
            number = pr.get("number") or event.get("number")
        if not number:
            log("不是 PR 事件，跳过（手动触发请填 pr 输入项）")
            return 0
        diff = http("GET", "%s/repos/%s/pulls/%d" % (GITHUB_API, repo, number),
                    token=token, accept="application/vnd.github.v3.diff", raw=True)

    if not diff.strip():
        log("diff 为空，跳过")
        return 0

    files, add, dele = changed_files(diff)
    secrets_found = scan_secrets(diff)
    log("改动文件 %d 个，+%d/-%d 行" % (len(files), add, dele))
    if secrets_found:
        log("!! 检出疑似密钥 %d 处" % len(secrets_found))

    if not api_key:
        log("未配置 REVIEW_API_KEY（仓库 Secret: DEEPSEEK_API_KEY），跳过 AI 审查")
        return 0

    limit = int(os.environ.get("REVIEW_MAX_DIFF") or 60000)
    truncated = len(diff) > limit
    user = "PR 标题：%s\nPR 描述：%s\n\n以下为本次 diff：\n%s%s" % (
        pr.get("title", ""), (pr.get("body") or "（无）")[:2000],
        diff[:limit], "\n\n（注意：diff 过长已截断，只评审以上部分）" if truncated else "")

    base = (os.environ.get("REVIEW_API_BASE") or "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("REVIEW_MODEL") or "deepseek-chat"
    payload = {"model": model,
               "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": user}],
               "temperature": 0.2, "max_tokens": 1600, "stream": False}
    try:
        out = http("POST", base + "/chat/completions", token=api_key, data=payload,
                   accept="application/json", timeout=180)
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

    review = redact(review)      # 模型可能把密钥原文抄进评审，先脱敏再打印/发布
    log(review)
    files_md = "\n".join("- %s" % f for f in files[:30])
    if len(files) > 30:
        files_md += "\n- ……另有 %d 个文件" % (len(files) - 30)
    secret_md = ""
    if secrets_found:
        # 只给位置与类型，绝不回显命中内容：评论是公开的，贴出片段等于二次泄漏
        secret_md = ("\n### 🔴 密钥扫描未通过\n\n检出疑似密钥 %d 处，请立刻删除并**轮换**该密钥"
                     "（为避免二次泄漏，此处不展示命中内容）：\n\n%s\n"
                     % (len(secrets_found),
                        "\n".join("- %s：%s（命中 %d 字符）" % it for it in secrets_found[:10])))
    comment = ("%s\n## 🤖 自动代码审查（%s）\n\n**改动**：%d 个文件，+%d/-%d 行%s\n\n"
               "<details><summary>改动文件</summary>\n\n%s\n\n</details>\n%s\n### 评审意见\n\n%s\n\n---\n"
               "<sub>由 GitHub Actions 自动生成，仅供参考，不作为合并门禁；"
               "调整规则见 .github/workflows/ai-review.yml。</sub>"
               % (MARKER, model, len(files), add, dele,
                  "（diff 过长已截断）" if truncated else "", files_md, secret_md, review))

    upsert_comment(repo, number, token, redact(comment), dry_run=a.dry_run)

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
