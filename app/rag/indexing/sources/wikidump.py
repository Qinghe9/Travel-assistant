# -*- coding: utf-8 -*-
"""中文维基百科 dump 分片读取（不走 API，不受限流影响）。

原理：multistream 索引给出「条目名 -> 分片字节偏移」（实测 4,969,911 条 / 49,703 个分片），
用 HTTP Range 只取该分片，解压后即为 100 个左右的 <page> 元素，从中挑出需要的条目。
"""

from __future__ import annotations

import bz2
import io
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://dumps.wikimedia.org/zhwiki/latest/"
IDX_URL = BASE + "zhwiki-latest-pages-articles-multistream-index.txt.bz2"
MS_URL = BASE + "zhwiki-latest-pages-articles-multistream.xml.bz2"
UA = {"User-Agent": "travel-assistant-rag/0.1 (course project; RAG knowledge base)"}

# 优先带行政后缀（裸名多为重定向页）
CANDIDATE_SUFFIXES = ("市", "地区", "自治州", "盟", "特别行政区", "县", "区", "")


def download_index(dest: Path, force=False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return dest
    req = urllib.request.Request(IDX_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=900) as resp, dest.open("wb") as fh:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    return dest


def load_index(idx_path: Path) -> dict:
    """返回 {标题: 分片偏移}。"""
    idx = {}
    with bz2.open(idx_path, "rb") as fh:
        for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
            parts = line.rstrip("\n").split(":", 2)
            if len(parts) == 3:
                idx[parts[2]] = int(parts[0])
    return idx


def resolve_titles(names, index: dict, suffixes=CANDIDATE_SUFFIXES) -> dict:
    """离线标题解析（不需要网络）：{原名: 索引中的真实标题}。"""
    out = {}
    for n in names:
        for suf in suffixes:
            cand = n + suf
            if cand in index:
                out[n] = cand
                break
    return out


def _page_text(el):
    t = el.find("*/{*}text")
    if t is None:
        t = el.find("{*}text")
    return t.text if t is not None and t.text else ""


def _decompress_stream(off, initial=600_000, cap=3_000_000):
    size = initial
    xml = b""
    while size <= cap:
        req = urllib.request.Request(MS_URL, headers=dict(UA, Range="bytes=%d-%d" % (off, off + size)))
        data = urllib.request.urlopen(req, timeout=180).read()
        dec = bz2.BZ2Decompressor()
        try:
            xml = dec.decompress(data)
        except Exception:  # noqa: BLE001
            return b""
        if dec.eof:
            return xml
        size *= 2
    return xml


def fetch_streams(offset_to_titles: dict, workers=8, progress=None):
    """{偏移: [标题...]} -> {标题: wikitext}。"""
    total = [0]

    def one(item):
        off, titles = item
        want = set(titles)
        got = {}
        try:
            xml = _decompress_stream(off)
            if not xml:
                return got
            root = ET.fromstring(b"<root>" + xml + b"</root>")
            for el in root:
                if el.tag.split("}")[-1] != "page":
                    continue
                t = el.findtext("{*}title")
                if t in want:
                    got[t] = _page_text(el)
            return got
        except Exception:  # noqa: BLE001
            return got

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got in ex.map(one, offset_to_titles.items()):
            out.update(got)
            total[0] += 1
            if progress and total[0] % 100 == 0:
                progress(total[0], len(offset_to_titles))
    return out
