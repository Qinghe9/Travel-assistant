# -*- coding: utf-8 -*-
"""Wikivoyage 数据源适配器：解析 dump、按 ispartof 重建条目树、选出目标范围。"""

from __future__ import annotations

import bz2
import re
from dataclasses import dataclass, field
from typing import Iterator

import xml.etree.ElementTree as ET

ISPARTOF = re.compile(r"\{\{\s*ispartof\s*\|([^}|]+)", re.I)


@dataclass
class RawPage:
    title: str
    wikitext: str
    parent: str | None = None
    root_city: str | None = None
    depth: int = 0
    children: list = field(default_factory=list)


def iter_pages(dump_path) -> Iterator[tuple[str, str]]:
    """流式读取 dump，产出 (标题, wikitext)；只取正文命名空间。"""
    with bz2.open(dump_path, "rb") as fh:
        for _ev, el in ET.iterparse(fh, events=("end",)):
            tag = el.tag.split("}")[-1]
            if tag == "ns":
                cur_ns = (el.text or "").strip()
            elif tag == "title":
                cur_title = (el.text or "").strip()
            elif tag == "text":
                cur_text = el.text or ""
            elif tag == "page":
                if cur_ns == "0":
                    yield cur_title, cur_text
                el.clear()


def build_tree(pages: dict) -> dict:
    """用 ispartof 重建父子关系。"""
    tree = {t: RawPage(title=t, wikitext=x) for t, x in pages.items()}
    for t, p in tree.items():
        m = ISPARTOF.search(p.wikitext)
        if m:
            p.parent = m.group(1).strip()
    for t, p in tree.items():
        if p.parent and p.parent in tree:
            tree[p.parent].children.append(t)
    return tree


def select_scope(tree: dict, cities: list) -> dict:
    """选出给定城市及其全部下级条目；root_city 记为遍历起点城市，depth 相对该城市。"""
    scope, deep = {}, {}
    for city in cities:
        if city not in tree:
            continue
        stack = [(city, 0)]
        while stack:
            t, d = stack.pop()
            if t in deep:
                continue
            deep[t] = d
            scope[t] = tree[t]
            scope[t].root_city = city
            scope[t].depth = d
            stack.extend((c, d + 1) for c in tree[t].children)
    return scope


def _root_of(tree: dict, title: str) -> str:
    """向上走到顶层条目（无 ispartof 父节点）的标题。"""
    seen, cur = set(), title
    while True:
        if cur in seen or cur not in tree:
            return title
        seen.add(cur)
        p = tree[cur].parent
        if not p or p not in tree:
            return cur
        cur = p
