# -*- coding: utf-8 -*-
"""语义切分：按 H2/H3 分节，块内按句子聚合到 350-600 字、重叠 60-100 字。"""

from __future__ import annotations

import hashlib
import pathlib as _pathlib
import re
import sys as _sys

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[3]))
from app.rag import config as cfg  # noqa: E402

# 标题行：容忍首尾 = 数量不一致（维基里存在 ==== X === 这种写法）
HEADING_RE = re.compile(r"^(={2,4})\s*(.+?)\s*=*\s*$")
# 维基里的存档/编辑标记（方括号可能已被上游清洗掉一半，所以两侧括号都可选）
_ARCHIVE = re.compile(r"\s*\[?\s*(页面存档|頁面存檔|存档|存檔|编辑|編輯)\s*\]?\s*$")
_BAD_CHARS = re.compile(r"[\[\]|<>]")

# 注意：不能按中文分号「；」切句 —— 它是 POI 卡片内部的字段分隔符
# （「武侯祠（地址：…；电话：…；开放时间：…）」），按它切会把卡片拦腰截断、丢掉 POI 名。
SENT_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=\n)")

LISTING_MARK_RE = re.compile(r"\x01LIST:([a-z]+)\x01")
LISTING_CATEGORY = {
    "see": "attraction", "do": "attraction", "marker": "attraction",
    "eat": "food", "drink": "food",
    "sleep": "hotel",
    "buy": "tips", "listing": "tips", "go": "transport", "vcard": "tips", "around": "transport",
}
CATEGORY_PRIORITY = ["attraction", "food", "hotel", "transport", "tips"]
CATEGORY_KEYWORDS = [
    ("food", ("餐", "吃", "美食", "小吃", "菜")),
    ("hotel", ("住宿", "酒店", "旅馆", "旅店", "宾馆", "民宿")),
    ("transport", ("交通", "抵达", "到达", "航空", "铁路", "客车", "机场", "火车站", "地铁", "公交", "自驾")),
    ("attraction", ("观光", "景点", "景区", "游览", "公园", "博物馆", "活动", "夜生活", "购物")),
]


def clean_heading(name: str):
    """清洗小节名；不可用的返回 None（该行当正文处理，不进 title_path）。"""
    name = name or ""
    for _ in range(3):                       # 可能连续挂多个标记
        new = _ARCHIVE.sub("", name)
        if new == name:
            break
        name = new
    name = name.replace("{", "").replace("}", "").replace("[", "").replace("]", "")
    name = name.strip("= \t\u3000").strip()
    name = re.sub(r"\s{2,}", " ", name)
    if not name or len(name) > 24 or _BAD_CHARS.search(name):
        return None
    return name


def iter_sections(text: str):
    """按 H2/H3 切成语义块，产出 (title_path 列表, 正文)。"""
    lines = text.split("\n")
    stack, buf, out = [], [], []
    for ln in lines:
        m = HEADING_RE.match(ln.strip())
        if m:
            body = "\n".join(buf).strip()
            if body:
                out.append((list(stack), body))
            buf = []
            level, name = len(m.group(1)), clean_heading(m.group(2))
            if name is None:              # 伪标题：该行当正文保留
                buf.append(ln)
                continue
            stack = stack[: level - 2] + [name]
            continue
        buf.append(ln)
    body = "\n".join(buf).strip()
    if body:
        out.append((list(stack), body))
    return out


def split_sentences(text: str):
    return [s for s in SENT_SPLIT.split(text) if s and s.strip()]


def _tail_overlap(buf: str, overlap: int, hard_max: int):
    """从 buf 末尾按整句取回至少 1 句、尽量到 overlap 字（目标 60-100 字）。"""
    tail, sents = "", split_sentences(buf)
    for s in reversed(sents):
        if tail and len(tail) + len(s) > hard_max:
            break
        tail = s + tail
        if len(tail) >= overlap:
            break
    return tail


def category_of(title_path, text=""):
    for name in reversed(title_path):
        for cat, kws in CATEGORY_KEYWORDS:
            if any(k in name for k in kws):
                return cat
        if name in cfg.CATEGORY_MAP:
            return cfg.CATEGORY_MAP[name]
    head = title_path[0] if title_path else ""
    for cat, kws in CATEGORY_KEYWORDS:
        if any(k in head for k in kws):
            return cat
    return "tips"


def category_from_listing(text: str):
    """从块内的清单标记推断 category；没有标记返回 None。"""
    cats = {LISTING_CATEGORY.get(m, "tips") for m in LISTING_MARK_RE.findall(text)}
    if not cats:
        return None
    for c in CATEGORY_PRIORITY:
        if c in cats:
            return c
    return "tips"


def _hard_split(s: str, max_len: int):
    """把超长句按中文逗号/顿号切成 <= max_len 的片段。"""
    if len(s) <= max_len:
        return [s]
    out, cur = [], ""
    for part in re.split(r"(?<=[，、,])", s):
        if len(cur) + len(part) <= max_len:
            cur += part
        else:
            if cur:
                out.append(cur)
            while len(part) > max_len:
                out.append(part[:max_len])
                part = part[max_len:]
            cur = part
    if cur:
        out.append(cur)
    return out


def _units(text: str, max_len: int):
    """把正文切成聚合单元：整行不超过上限时以「行」为单位（保护 POI 卡片不被拦腰截断），
    超长行才退化为按句切分。"""
    units = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= max_len:
            units.append(line)
        else:
            units.extend(split_sentences(line))
    return units


def chunk_text(text: str, size: int = 450, overlap: int = 80, min_len: int = 200, max_len: int = 600):
    """块内按句子聚合；相邻块保留 60-100 字重叠，重叠边界对齐到句子。"""
    sents = _units(text, max_len)
    chunks, buf = [], ""
    for s in sents:
        if len(buf) + len(s) <= max_len:
            buf += s
            continue
        if buf:
            chunks.append(buf)
            buf = _tail_overlap(buf, overlap, overlap + 220) + s
        else:                       # 单句超长：按逗号/顿号再切，兜底硬切
            for piece in _hard_split(s, max_len):
                chunks.append(piece)
            buf = ""
    if buf.strip():
        chunks.append(buf)
    merged = []
    for c in chunks:
        if merged and len(c) < min_len and len(merged[-1]) + len(c) <= max_len:
            merged[-1] += c
        else:
            merged.append(c)
    # 兜底：任何超过 max_len 的块强制再切（维基里存在无句号、无换行的超长清单）
    final = []
    for c in merged:
        final.extend(_hard_split(c, max_len) if len(c) > max_len else [c])
    return [c.strip() for c in final if len(c.strip()) >= 60]


def merge_short(chunks: list, min_len: int = 200, max_len: int = 600):
    """跨小节把过短的块向前/向后合并，减少 <200 字的碎片。"""
    def join(a, b):
        a["text"] = a["text"].rstrip() + "\n" + b["text"].lstrip()
        a["char_len"] = len(a["text"])
        return a

    out = []
    for c in chunks:
        if out and c["char_len"] < min_len and out[-1]["char_len"] + c["char_len"] + 1 <= max_len:
            join(out[-1], c)
            continue
        out.append(c)
    res, i = [], 0
    while i < len(out):
        c = out[i]
        if c["char_len"] < min_len and i + 1 < len(out) and c["char_len"] + out[i + 1]["char_len"] + 1 <= max_len:
            join(c, out[i + 1])
            i += 2
        else:
            i += 1
        res.append(c)
    return res


def split_document(doc: dict, size: int = 450, overlap: int = 80):
    """产出 chunk 记录列表（含全部过滤字段）。"""
    raw = []
    for title_path, body in iter_sections(doc["text"]):
        path = ([doc["title"]] + title_path) if title_path else [doc["title"]]
        for piece in chunk_text(body, size=size, overlap=overlap):
            raw.append({"title_path": " > ".join(path),
                        "category": category_of(title_path, body),
                        "text": piece, "char_len": len(piece)})

    raw = merge_short(raw)

    out, idx = [], 0
    for c in raw:
        lcat = category_from_listing(c["text"])
        text = LISTING_MARK_RE.sub("", c["text"]).strip()
        if len(text) < 60:
            continue
        out.append({
            "chunk_id": "%s#%04d" % (doc["doc_id"], idx),
            "parent_doc_id": doc["doc_id"],
            "doc_title": doc["title"],
            "title_path": c["title_path"],
            "text": text,
            "char_len": len(text),
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "chunk_index": idx,
            "province": doc.get("province"),
            "city": doc.get("city"),
            "county": doc.get("county"),
            # 只有"整块以 POI 卡片为主"（卡片在块首）时才用卡片类别覆盖小节类别。
            # 原实现 lcat or c["category"] 会让"散文段里嵌了一张 {{see}} 卡片"的整块变成 attraction，
            # 实测后果：标题路径为「广州 > 广州交通/其它」、正文讲英语水平的整段被标成 attraction，
            # 按 category 过滤形同虚设，"推荐景点"类问题返回的全是概述段落。
            "category": lcat if (lcat and c["text"].lstrip().startswith("\x01LIST:")) else c["category"],
            "source_type": doc.get("source_type", "public"),
            "source_url": doc.get("source_url"),
            "license": doc.get("license"),
            "retrieved_at": doc.get("retrieved_at"),
            "updated_at": doc.get("retrieved_at"),
            "lang": doc.get("lang", "zh"),
        })
        idx += 1
    return out
