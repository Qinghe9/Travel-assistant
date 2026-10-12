# -*- coding: utf-8 -*-
"""中文维基百科数据源适配器：按标题批量取纯文本 + 旅游相关性章节筛选。

许可：CC BY-SA 4.0。使用 action=query&prop=extracts&explaintext 得到纯文本；
再按章节标题做旅游相关性筛选，避免引入无关知识。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

API = "https://zh.wikipedia.org/w/api.php"
REST = "https://api.wikimedia.org/core/v1/wikipedia/zh/page/"
UA = "travel-assistant-rag/0.1 (course project; RAG knowledge base)"

KEEP = ("地理", "位置", "气候", "交通", "旅游", "景点", "名胜", "文化", "饮食", "美食", "历史",
        "沿革", "得名", "概况", "简介", "自然", "环境", "物产", "特产", "行政区划", "区划",
        "建筑", "地标", "公园", "博物馆", "节庆", "方言")
DROP = ("政治", "政府", "经济", "产业", "人口", "民族", "教育", "体育", "卫生", "人物", "友好城市",
        "参考文献", "外部链接", "注释", "参见", "军事", "司法", "媒体", "荣誉", "规划", "基础设施",
        "金融", "商业", "科技", "医疗", "外交", "党委", "人大", "政协", "党史", "争议", "对外交流",
        "友好关系", "城市荣誉", "著名人物", "历史名人", "经济发展", "社会事业")

HEADING = re.compile(r"^(={2,4})\s*(.+?)\s*\1\s*$", re.M)
SUFFIXES = ("市", "地区", "自治州", "盟", "县", "区", "特别行政区", "省")
_THROTTLE = {"last": 0.0, "gap": 0.35}


def _proxy():
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if p:
        os.environ.setdefault("HTTPS_PROXY", p)
        os.environ.setdefault("HTTP_PROXY", p)
    return p


def _throttle():
    now = time.time()
    wait = _THROTTLE["gap"] - (now - _THROTTLE["last"])
    if wait > 0:
        time.sleep(wait)
    _THROTTLE["last"] = time.time()


def _api(params, timeout=40):
    _proxy()
    params = dict(params, format="json")
    u = API + "?" + urllib.parse.urlencode(params)
    d = json.load(urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout))
    pages = d.get("query", {}).get("pages", {})
    for p in pages.values():
        if "missing" in p:
            continue
        txt = p.get("extract") or ""
        if txt.strip():
            return p.get("title", ""), txt
    return None, ""


def _rest_wikitext(title, timeout=40):
    """备选通道：api.wikimedia.org REST（与 action API 限流相互独立）。"""
    _proxy()
    u = REST + urllib.parse.quote(title.replace(" ", "_"))
    r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout)
    d = json.loads(r.read().decode("utf-8", "replace"))
    return d.get("source") or ""


def batch_resolve(titles, suffixes=("", "市", "地区", "自治州", "盟"), timeout=40):
    """批量把地名解析成真实条目名：先原名，再补行政后缀；每 50 个一批。"""
    resolved, remaining = {}, list(dict.fromkeys(titles))
    for suf in suffixes:
        if not remaining:
            break
        for i in range(0, len(remaining), 50):
            chunk = remaining[i:i + 50]
            sent = {t + suf: t for t in chunk}
            try:
                _throttle()
                u = API + "?" + urllib.parse.urlencode({
                    "action": "query", "prop": "info", "redirects": 1, "format": "json",
                    "titles": "|".join(sent)})
                d = json.load(urllib.request.urlopen(
                    urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout))
            except Exception:  # noqa: BLE001
                continue
            q = d.get("query", {})
            chain = {}
            for n in q.get("normalized", []):
                chain[n["from"]] = n["to"]
            for r in q.get("redirects", []):
                chain[r["from"]] = r["to"]

            def final(x):
                seen = set()
                while x in chain and x not in seen:
                    seen.add(x)
                    x = chain[x]
                return x

            alive = set()
            for p in q.get("pages", {}).values():
                if "missing" not in p:
                    alive.add(p.get("title"))
            for c, orig in sent.items():
                f = final(c)
                if f in alive:
                    resolved[orig] = f
        remaining = [t for t in remaining if t not in resolved]
    return resolved


def fetch_extract(title, timeout=40, retries=4):
    """取单篇正文（标题已由 batch_resolve 定好）：action API 纯文本优先，REST 兜底。"""
    _proxy()
    import urllib.error

    from app.rag.indexing import clean as C
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            _throttle()
            t, txt = _api({"action": "query", "prop": "extracts", "explaintext": 1,
                           "redirects": 1, "titles": title}, timeout)
            if txt:
                return t, txt
            _throttle()
            src = _rest_wikitext(title, timeout)
            if src:
                return title, C.normalize(C.strip_markup(C.expand_templates(src)))
            return title, ""
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s" % e.code
            if e.code in (429, 503):
                time.sleep(min(30, 4 * attempt * attempt))
                continue
            time.sleep(1.5 * attempt)
        except Exception as e:  # noqa: BLE001
            last_err = type(e).__name__
            time.sleep(1.5 * attempt)
    return title, ""


def fetch_many(titles, workers=10, progress=None):
    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (t, txt) in enumerate(ex.map(fetch_extract, titles), 1):
            out[t] = txt
            if progress and i % 50 == 0:
                progress(i, len(titles))
    return out


def filter_travel_sections(text: str, keep_all_lead=True):
    """按章节标题筛掉旅游无关内容；默认只保留导语与命中 KEEP 的章节。"""
    if not text:
        return ""
    parts, state = [], {"head": None}
    buf = []

    def flush():
        body = "\n".join(buf).strip()
        if not body:
            return
        head = state["head"]
        if head is None:
            if keep_all_lead:
                parts.append(body)
            return
        if any(k in head for k in DROP):
            return
        if any(k in head for k in KEEP):
            parts.append("== %s ==\n%s" % (head, body))

    for line in text.split("\n"):
        m = HEADING.match(line.strip())
        if m:
            flush()
            state["head"], buf = m.group(2).strip(), []
            continue
        buf.append(line)
    flush()
    return "\n".join(parts)
