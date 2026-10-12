# -*- coding: utf-8 -*-
"""清洗：去模板与噪声、清单模板转 POI 卡片、文本规范化、段落去重。"""

from __future__ import annotations

import hashlib
import re
from collections import Counter

DROP_TEMPLATES = {
    "pagebanner", "printdistricts", "citybar", "regionlist", "mapframe", "mapshape", "geo", "ispartof",
    "stdcolor", "flag", "lang", "outlinecity", "outline", "usablecity", "guidecity", "starcity", "stub",
    "translate", "cleanup", "expand", "vfd", "infobox", "weather box", "climate", "routebox", "otheruses",
    "disamb", "coord", "commons", "wikipedia", "sisterlinks", "related", "portal", "tl", "toc", "toc limit",
    "pagebanner/styles.css", "mapshape/styles.css", "maplink", "mapframe/styles.css", "reflist",
}
LISTING_TEMPLATES = {"see", "do", "buy", "eat", "drink", "sleep", "listing", "vcard", "go", "marker", "around"}
SHORT_MAP = {
    "航空": "航空", "铁路": "铁路", "客车": "客车", "用餐": "用餐", "住宿": "住宿", "醫院": "医院",
    "医院": "医院", "购物": "购物", "观光": "观光", "活动": "活动", "安全": "安全", "下一站": "下一站",
}
SECTION_DROP = {"下一站", "外部链接", "外部連結", "参考", "參考", "注释", "註釋", "参见", "參見", "链接", "連結"}
FIELD_LABEL = {
    "address": "地址", "phone": "电话", "hours": "开放时间", "price": "门票", "checkin": "入住",
    "checkout": "退房", "lastedit": "", "content": "", "name": "", "alt": "别名", "url": "网址",
    "email": "邮箱", "fax": "传真", "lat": "", "long": "", "type": "", "image": "", "wikidata": "",
    "wikipedia": "", "zoom": "", "group": "", "show": "", "format": "", "directions": "方位",
}
NOISE_PATTERNS = [
    re.compile(r"^\s*__[A-Z]+__\s*$"),
    re.compile(r"^\s*\{\|"), re.compile(r"^\s*\|\}"), re.compile(r"^\s*\|[-+]"),
    re.compile(r"^\s*<[^>]+>\s*$"),
    re.compile(r"^\s*$"),
]
CATEGORY_RE = re.compile(r"^\s*\[\[(?:Category|分类|分類|Cat)\s*:[^\]]*\]\]\s*$", re.I)
INTERLANG_RE = re.compile(r"^\s*\[\[[a-z]{2,3}(?:-[a-z]+)?\s*:[^\]]*\]\]\s*$")

ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)

REDIRECT_RE = re.compile(r"^\s*#\s*(?:REDIRECT|重定向|重新導向|重定向到)\s*:?\s*\[\[([^\]]+)\]\]", re.I | re.M)
MIN_KEEP_CHARS = 80

try:
    import zhconv  # 繁->简，保证检索时简繁一致
except Exception:  # noqa: BLE001
    zhconv = None


def is_redirect(wikitext: str):
    """返回重定向目标标题；不是重定向则返回 None。"""
    m = REDIRECT_RE.match(wikitext or "")
    if m:
        return m.group(1).split("|")[0].strip()
    return None


def to_simplified(s: str) -> str:
    if zhconv is None:
        return s
    try:
        return zhconv.convert(s, "zh-cn")
    except Exception:  # noqa: BLE001
        return s


def _find_top_template(s: str, i: int):
    """从 s[i:i+2]=="{{" 开始，找配对的 }}，返回 (name, params, end)。支持嵌套。"""
    depth, j = 0, i
    while j < len(s):
        if s.startswith("{{", j):
            depth += 1
            j += 2
        elif s.startswith("}}", j):
            depth -= 1
            j += 2
            if depth == 0:
                inner = s[i + 2: j - 2]
                parts = _split_params(inner)
                name = parts[0].strip().lower() if parts else ""
                return name, parts[1:], j
        else:
            j += 1
    return None, None, len(s)


def _split_params(inner: str) -> list:
    """按顶层 | 拆分模板参数（忽略嵌套 {{}} 与 [[]] 内的 |）。"""
    parts, buf, tb, sb = [], [], 0, 0
    i = 0
    while i < len(inner):
        if inner.startswith("{{", i):
            tb += 1; buf.append("{{"); i += 2; continue
        if inner.startswith("}}", i):
            tb -= 1; buf.append("}}"); i += 2; continue
        if inner.startswith("[[", i):
            sb += 1; buf.append("[["); i += 2; continue
        if inner.startswith("]]", i):
            sb -= 1; buf.append("]]"); i += 2; continue
        c = inner[i]
        if c == "|" and tb == 0 and sb == 0:
            parts.append("".join(buf)); buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return parts


def _params(pairs: list) -> dict:
    out = {}
    for p in pairs:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip().lower()] = v.strip()
        elif p.strip():
            out.setdefault("_pos", []).append(p.strip())
    return out


def expand_templates(s: str, listing_texts: list | None = None) -> str:
    """把模板展开成可读文本：清单模板转 POI 卡片，噪声模板丢弃。"""
    out, i = [], 0
    while True:
        j = s.find("{{", i)
        if j < 0:
            out.append(s[i:]); break
        out.append(s[i:j])
        name, pairs, end = _find_top_template(s, j)
        if name is None:
            out.append(s[j:]); break
        body = "|".join(pairs)
        body = expand_templates(body, listing_texts)
        if name in DROP_TEMPLATES:
            i = end; continue
        if name in SHORT_MAP:
            out.append("\n### " + SHORT_MAP[name] + "\n"); i = end; continue
        prm = _params(_split_params(body))
        if name in LISTING_TEMPLATES:
            txt = _render_listing(name, prm)
            if listing_texts is not None:
                listing_texts.append({"template": name, **prm})
            out.append(txt); i = end; continue
        if name == "cquote":
            out.append(prm.get("_pos", [""])[0] if prm.get("_pos") else ""); i = end; continue
        for key in ("content", "description", "说明", "text"):
            if prm.get(key):
                out.append(prm[key]); break
        i = end
    return "".join(out)


LISTING_MARK = "\x01LIST:%s\x01"


def _render_listing(name: str, prm: dict) -> str:
    pos = prm.get("_pos", [])
    title = prm.get("name") or (pos[0] if pos else "")
    content = prm.get("content") or prm.get("description") or (pos[1] if len(pos) > 1 else "")
    mark = LISTING_MARK % name
    bits = []
    for k, label in FIELD_LABEL.items():
        v = prm.get(k, "")
        if v and label and k != "name" and k != "content":
            bits.append("%s：%s" % (label, v))
    tail = "（%s）" % "；".join(bits) if bits else ""
    # 属性紧跟在名字后面：即使描述被拆句，POI 名与地址/电话/开放时间也留在同一句里
    if title and content:
        return "\n%s%s%s：%s\n" % (mark, title, tail, content)
    if title:
        return "\n%s%s%s\n" % (mark, title, tail)
    return "\n%s%s\n" % (mark, content) if content else ""


def strip_markup(s: str) -> str:
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<(ref|references|gallery|timeline|mapframe|maplink|div|span|center|small|big|sup|sub|code|nowiki|poem|syntaxhighlight)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]{1,80}>", " ", s)
    s = re.sub(r"\{\|.*?\|\}", " ", s, flags=re.S)
    s = re.sub(r"\[\[(?:File|Image|文件|檔案|Category|分类|分類|Cat)\s*:[^\]]*\]\]", " ", s, flags=re.I)
    s = re.sub(r"\[\[[a-z]{2,3}(?:-[a-z]+)?\s*:[^\]]*\]\]", " ", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[(https?://\S+)\s+([^\]]+)\]", r"\2", s)
    s = re.sub(r"\[https?://\S+\]", " ", s)
    s = re.sub(r"'{2,5}", "", s)
    s = re.sub(r"^[#*:;]+\s*", "", s, flags=re.M)
    s = re.sub(r"[ \t\u00a0]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


def drop_noise_sections(s: str) -> str:
    """删除噪声章节（以 == 标题 分节）。"""
    lines = s.split("\n")
    out, skipping, level = [], False, 0
    for ln in lines:
        m = re.match(r"^(={2,4})\s*(.+?)\s*\1\s*$", ln.strip())
        if m:
            lvl, name = len(m.group(1)), m.group(2).strip()
            if name in SECTION_DROP:
                skipping, level = True, lvl
                continue
            if skipping and lvl <= level:
                skipping = False
            out.append(ln if not skipping else "")
            continue
        if (not skipping and not any(p.match(ln) for p in NOISE_PATTERNS)
                and not CATEGORY_RE.match(ln) and not INTERLANG_RE.match(ln)):
            out.append(ln)
    return "\n".join(out)


def normalize(s: str) -> str:
    s = s.translate(ZERO_WIDTH)
    s = re.sub(r"[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]", lambda m: chr(ord(m.group()) - 0xFEE0), s)
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    s = re.sub(r"([，。！？；：])\1+", r"\1", s)
    return to_simplified(s).strip()


def shingles(text: str, k: int = 12) -> set:
    t = re.sub(r"\s+", "", text)
    return {t[i:i + k] for i in range(max(len(t) - k + 1, 1))}


def fingerprint(text: str, k: int = 12, samples: int = 4):
    """确定性指纹：抽样若干 k-shingle 的 blake2b，用于跨文档近似去重（O(1) 判定）。"""
    t = re.sub(r"\s+", "", text)
    if len(t) <= k:
        return (hashlib.blake2b(t.encode("utf-8"), digest_size=8).digest(),)
    step = max((len(t) - k) // samples, 1)
    return tuple(hashlib.blake2b(t[i:i + k].encode("utf-8"), digest_size=8).digest()
                 for i in range(0, len(t) - k + 1, step))[:samples]


def dedup_paragraphs(paras, threshold: float = 0.9, seen_global=None, seen_fp=None):
    """段落级去重：精确哈希 + 指纹（跨文档）+ shingle Jaccard（文档内）。"""
    kept, sigs, dropped = [], [], 0
    for p in paras:
        p = p.strip()
        if len(p) < 8:
            continue
        h = hashlib.sha1(p.encode("utf-8")).hexdigest()
        if seen_global is not None:
            if h in seen_global:
                dropped += 1
                continue
            seen_global.add(h)
        fp = fingerprint(p)
        if seen_fp is not None:
            if fp in seen_fp:
                dropped += 1
                continue
            seen_fp.add(fp)
        sg = shingles(p)
        if sigs and any(len(sg & o) / max(len(sg | o), 1) >= threshold for o in sigs):
            dropped += 1
            continue
        sigs.append(sg)
        kept.append(p)
    return kept, dropped
