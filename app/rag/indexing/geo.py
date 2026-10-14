# -*- coding: utf-8 -*-
"""GeoNames 地名索引：省级/地级/县级归属 + 别名词表，一次扫描同时产出。

- 省份用 34 个省级行政区规范名（简体），ADM1 的英文/异体名归一化后落到规范名。
- 直辖市（北京/天津/上海/重庆/香港/澳门）同时按 level=city 登记，便于按 city 过滤。
- ADM2（地级市）→ city；ADM3（区县）→ county，city 通过 GeoNames admin2 码回连。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict

CJK = re.compile(r"[\u4e00-\u9fff]{2,}")

PROVINCES = ["北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏",
             "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南", "广东", "广西",
             "海南", "重庆", "四川", "贵州", "云南", "西藏", "陕西", "甘肃", "青海", "宁夏",
             "新疆", "香港", "澳门", "台湾"]
MUNICIPALITIES = {"北京", "天津", "上海", "重庆", "香港", "澳门"}

# GeoNames 的 CN.zip 不含台湾，港澳台子页面又大量使用“A/B”标题。若只拿 B
# 去匹配大陆同名区县，会把“臺北/松山”错挂到赤峰、“新北/三峡”错挂到常州。
# 这里登记可确定的一级目的地；区级 B 只有在与 A 的省市归属一致时才会采用。
SPECIAL_ADMIN = {
    "台湾": ("台湾", None), "台北": ("台湾", "台北"), "新北": ("台湾", "新北"),
    "桃园": ("台湾", "桃园"), "台中": ("台湾", "台中"), "台南": ("台湾", "台南"),
    "高雄": ("台湾", "高雄"), "基隆": ("台湾", "基隆"), "新竹": ("台湾", "新竹"),
    "嘉义": ("台湾", "嘉义"), "宜兰": ("台湾", "宜兰"), "苗栗": ("台湾", "苗栗"),
    "彰化": ("台湾", "彰化"), "南投": ("台湾", "南投"), "云林": ("台湾", "云林"),
    "屏东": ("台湾", "屏东"), "台东": ("台湾", "台东"), "花莲": ("台湾", "花莲"),
    "澎湖": ("台湾", "澎湖"), "香港": ("香港", "香港"), "澳门": ("澳门", "澳门"),
}

PROV_FALLBACK = {
    "Beijing Shi": "北京", "Tianjin Shi": "天津", "Shanghai Shi": "上海", "Chongqing Shi": "重庆",
    "Hebei": "河北", "Shanxi": "山西", "Inner Mongolia Autonomous Region": "内蒙古",
    "Liaoning": "辽宁", "Jilin": "吉林", "Heilongjiang": "黑龙江", "Jiangsu": "江苏",
    "Zhejiang": "浙江", "Anhui": "安徽", "Fujian": "福建", "Jiangxi": "江西", "Shandong": "山东",
    "Henan": "河南", "Hubei": "湖北", "Hunan": "湖南", "Guangdong": "广东",
    "Guangxi Zhuang Autonomous Region": "广西", "Hainan": "海南", "Sichuan": "四川",
    "Guizhou": "贵州", "Yunnan": "云南", "Tibet Autonomous Region": "西藏", "Shaanxi": "陕西",
    "Gansu": "甘肃", "Qinghai": "青海", "Ningxia Hui Autonomous Region": "宁夏",
    "Xinjiang Uyghur Autonomous Region": "新疆", "Hong Kong": "香港", "Macau": "澳门", "Taiwan": "台湾",
}
VARIANT_FIX = {"甘粛": "甘肃", "黒龍江": "黑龙江", "內蒙古": "内蒙古"}
SUFFIX = ("特别行政区", "特別行政區", "自治区", "自治州", "地区", "盟", "旗", "区", "县", "市", "省", "州", "镇", "乡")

try:
    import zhconv

    def _simp(s):
        return zhconv.convert(s, "zh-cn") if s else s
except Exception:  # noqa: BLE001
    def _simp(s):
        return s


def _norm(s):
    if not s:
        return s
    s = _simp(s)
    for k, v in VARIANT_FIX.items():
        s = s.replace(k, v)
    return s


def _cjk_alts(row):
    out = []
    for a in (row[3] or "").split(","):
        a = _norm(a.strip())
        if a and CJK.fullmatch(a) and a not in out:
            out.append(a)
    return out


def _canon(names):
    """区县/市：优先带行政后缀的短名。"""
    if not names:
        return None
    for suf in ("区", "县", "市", "旗", "州", "盟", "镇", "乡"):
        cands = [n for n in names if n.endswith(suf)]
        if cands:
            return min(cands, key=len)
    return min(names, key=len)


def _variants(n):
    """登记原名与去掉行政后缀的形式，便于「北京/西城」这类标题命中。"""
    out = [n]
    for suf in SUFFIX:
        if n.endswith(suf) and len(n) > len(suf) + 1:
            out.append(n[: -len(suf)])
            break
    return out


def _strip_city_suffix(n):
    return n[:-1] if n and n.endswith("市") and len(n) > 2 else n


class GeoIndex:
    """地名 -> (省, 市, 县) 归属；同时给出别名表。"""

    def __init__(self):
        self.adm1_code2prov = {}
        self.adm2_code2city = {}
        self.name2rec = {}
        self.alias2canon = dict(HAND_CURATED)
        self.canon2alias = defaultdict(set)
        for a, c in HAND_CURATED.items():
            self.canon2alias[c].add(a)
        for name, (prov, city) in SPECIAL_ADMIN.items():
            rec = dict(level="city" if city else "province", prov=prov, city=city,
                       county=None, lat=None, lon=None, geonameid=None)
            self.name2rec[name] = rec

    @classmethod
    def build(cls, zip_path, cache_path=None, corpus_text=None, corpus_names=None):
        if cache_path and cache_path.exists():
            return cls.load(cache_path)
        idx = cls()
        # 语料 n-gram 索引（2-4 字）：O(1) 判定地名是否出现在正文里，
        # 避免对 96 万行 GeoNames 逐行做 7MB 子串扫描。
        ngrams = {}
        corpus_names = set(corpus_names or ())
        long_cache = {}
        if not corpus_names and corpus_text is not None:
            for n in (2, 3, 4):
                ngrams[n] = {corpus_text[i:i + n] for i in range(len(corpus_text) - n + 1)}

        rows = []
        with zipfile.ZipFile(zip_path) as z, z.open("CN.txt") as fh:
            for line in io.TextIOWrapper(fh, encoding="utf-8"):
                c = line.rstrip("\n").split("\t")
                if len(c) >= 19:
                    rows.append(c)

        for r in rows:                                  # 省级
            if r[7] != "ADM1":
                continue
            names = _cjk_alts(r)
            canon = next((p for p in PROVINCES if p in names), None) or PROV_FALLBACK.get(r[1]) or _canon(names)
            if not canon:
                continue
            canon = _norm(canon)
            idx.adm1_code2prov[r[10]] = canon
            is_muni = canon in MUNICIPALITIES
            rec = dict(level="city" if is_muni else "province", prov=canon,
                       city=canon if is_muni else None, county=None,
                       lat=r[4], lon=r[5], geonameid=int(r[0]))
            for n in (names or [canon]) + [canon]:
                for v in _variants(n):
                    idx.name2rec.setdefault(v, rec)

        for r in rows:                                  # 地级市
            if r[7] != "ADM2":
                continue
            names = _cjk_alts(r)
            canon = _canon(names)
            if not canon:
                continue
            canon = _norm(canon)
            prov = idx.adm1_code2prov.get(r[10])
            canon = _strip_city_suffix(canon)
            idx.adm2_code2city[r[11]] = canon
            rec = dict(level="city", prov=prov, city=canon, county=None,
                       lat=r[4], lon=r[5], geonameid=int(r[0]))
            for n in names:
                for v in _variants(n):
                    idx.name2rec.setdefault(v, rec)

        for r in rows:                                  # 区县
            if r[7] != "ADM3":
                continue
            names = _cjk_alts(r)
            canon = _canon(names)
            if not canon:
                continue
            canon = _norm(canon)
            prov = idx.adm1_code2prov.get(r[10])
            city = idx.adm2_code2city.get(r[11])
            rec = dict(level="county", prov=prov, city=city, county=canon,
                       lat=r[4], lon=r[5], geonameid=int(r[0]))
            for n in names:
                for v in _variants(n):
                    idx.name2rec.setdefault(v, rec)

        if corpus_text is not None or corpus_names:     # 别名（只留语料中出现的规范名）
            for r in rows:
                if r[6] not in ("A", "P"):
                    continue
                names = _cjk_alts(r)
                if not names:
                    continue
                canon = _norm(min(names, key=len))
                if corpus_names:
                    if canon not in corpus_names:
                        continue
                elif ngrams:
                    if len(canon) in ngrams:
                        if canon not in ngrams[len(canon)]:
                            continue
                    else:
                        hit = long_cache.get(canon)
                        if hit is None:
                            hit = canon in corpus_text
                            long_cache[canon] = hit
                        if not hit:
                            continue
                else:
                    continue
                for n in names:
                    idx.alias2canon.setdefault(n, canon)
                    idx.canon2alias[canon].add(n)
                if r[2]:
                    idx.canon2alias[canon].add(r[2])

        if cache_path:
            idx.save(cache_path)
        return idx

    def resolve(self, title, text="", parents=()):
        """返回 (province, city, county)；只用标题、A/B 结构与 ispartof 链。"""
        title = _norm(title or "")
        if "/" in title:
            head, tail = title.split("/", 1)
            rec_h, rec_t = self.name2rec.get(head), self.name2rec.get(tail)
            # A/B 必须先可靠识别 A。只命中 B 时歧义极大，绝不能用它反推整篇归属。
            if rec_h:
                city = rec_h.get("city")
                county = rec_h.get("county") if rec_h.get("level") == "county" else None
                # B 仅在省份相同，且双方城市不冲突时作为下级行政区使用。
                compatible = (rec_t and rec_t.get("prov") == rec_h.get("prov")
                              and (not city or not rec_t.get("city") or rec_t.get("city") == city))
                if compatible:
                    city = city or rec_t.get("city")
                    if rec_t.get("level") in ("county", "city"):
                        county = rec_t.get("county") or (rec_t.get("city") if city != rec_t.get("city") else None)
                return rec_h.get("prov"), city, county
            # head 未识别时继续尝试父级，不允许降级到 tail。
        # 裸标题（如“五峰乡”“吉安乡”）在两岸可能同名。若 ispartof 父级能
        # 确定到城市，父级上下文比全国地名表中的同名命中更可靠。
        for p in parents:
            p_prov, p_city, p_county = self.resolve(p, parents=())
            if p_prov and p_city:
                rec = next((self.name2rec.get(c) for c in self._candidates(title)
                            if self.name2rec.get(c)), None)
                compatible = rec and rec.get("prov") == p_prov and (
                    not rec.get("city") or rec.get("city") == p_city)
                county = ((rec.get("county") or rec.get("city")) if compatible else p_county)
                return p_prov, p_city, county
        for cand in self._candidates(title):
            rec = self.name2rec.get(cand)
            if rec:
                return rec["prov"], rec["city"], rec["county"]
        for p in parents:
            for cand in self._candidates(p):
                rec = self.name2rec.get(cand)
                if rec:
                    return rec["prov"], rec["city"], rec["county"]
        return None, None, None

    @staticmethod
    def _candidates(title):
        out = [title]
        if "/" in title:
            head, _tail = title.split("/", 1)
            out.append(head)
        for suffix in SUFFIX:
            if title.endswith(suffix) and len(title) > len(suffix):
                out.append(title[: -len(suffix)])
        return out

    def save(self, path):
        path.write_text(json.dumps({
            "adm1_code2prov": self.adm1_code2prov,
            "adm2_code2city": self.adm2_code2city,
            "name2rec": self.name2rec,
            "alias_to_canonical": self.alias2canon,
            "canonical_to_aliases": {k: sorted(v) for k, v in self.canon2alias.items()},
        }, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path):
        d = json.loads(path.read_text(encoding="utf-8"))
        idx = cls()
        idx.adm1_code2prov = d["adm1_code2prov"]
        idx.adm2_code2city = d["adm2_code2city"]
        # 保留代码内的港澳台确定性映射；磁盘中的 CN GeoNames 记录只补充其余名称。
        for name, rec in d["name2rec"].items():
            idx.name2rec.setdefault(name, rec)
        idx.alias2canon = d["alias_to_canonical"]
        idx.canon2alias = defaultdict(set, {k: set(v) for k, v in d["canonical_to_aliases"].items()})
        return idx


HAND_CURATED = {
    "蓉城": "成都", "锦官城": "成都", "蓉": "成都",
    "魔都": "上海", "沪": "上海", "申城": "上海", "浦東": "浦东", "浦东新区": "浦东",
    "羊城": "广州", "穗": "广州", "花城": "广州",
    "鹏城": "深圳", "鹏": "深圳",
    "山城": "重庆", "渝": "重庆", "雾都": "重庆",
    "江城": "武汉", "汉": "武汉", "汉口": "武汉", "武昌": "武汉", "汉阳": "武汉",
    "六朝古都": "南京", "金陵": "南京", "宁": "南京",
    "姑苏": "苏州", "吴中": "苏州", "平江": "苏州",
    "杭城": "杭州", "临安": "杭州",
    "京城": "北京", "首都": "北京", "燕京": "北京", "京师": "北京",
}
