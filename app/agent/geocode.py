# -*- coding: utf-8 -*-
"""全国行政地名统一解析。

解析顺序固定为：
    1. data/scope_cities.json / data/scope_counties.json
    2. 高德 geocode（有 Key 时，结果落 SQLite 缓存）
    3. GeoNames GeoIndex（本地 ADM1/ADM2/ADM3）

三层都失败时返回 None，绝不根据常识编造坐标。
"""

from __future__ import annotations
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from app.rag import config as rag_cfg


_SCOPE_CACHE = None
_SCOPE_STAMP = None
_GEO_INDEX = None
_GEO_INDEX_LOCK = threading.Lock()
_SQL_LOCK = threading.Lock()
CACHE_FILE = rag_cfg.DATA / "agent" / "geocode_cache.sqlite"
CACHE_TTL_SECONDS = 30 * 24 * 3600

_ADMIN_SUFFIXES = ("特别行政区", "自治州", "自治区", "地区", "新区", "林区", "矿区",
                   "省", "市", "县", "区", "旗", "盟", "州")
_KEEP_SUFFIX = ("新区", "林区", "矿区")


def _clean(value) -> str:
    if isinstance(value, (list, tuple)):
        value = next((x for x in value if str(x or "").strip()), "")
    return str(value or "").strip()


def _short_name(value: str) -> str:
    name = _clean(value)
    for suffix in _ADMIN_SUFFIXES:
        if (name.endswith(suffix) and len(name) > len(suffix) + 1
                and not name.endswith(_KEEP_SUFFIX)):
            return name[:-len(suffix)]
    return name


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _trace(trace, message: str) -> None:
    if trace is not None:
        trace.append(message)


def _scope_paths() -> tuple[Path, ...]:
    paths = [Path(rag_cfg.SCOPE_FILE)]
    county_file = getattr(rag_cfg, "SCOPE_COUNTIES_FILE", None)
    if county_file:
        paths.append(Path(county_file))
    return tuple(paths)


def _load_scope() -> dict:
    """读取城市与县级 scope；文件更新后自动重载。"""
    global _SCOPE_CACHE, _SCOPE_STAMP
    paths = _scope_paths()
    stamp = tuple((str(path), path.stat().st_mtime_ns if path.exists() else 0)
                  for path in paths)
    with _SQL_LOCK:
        if _SCOPE_CACHE is not None and _SCOPE_STAMP == stamp:
            return _SCOPE_CACHE
        records = {}
        for path, _mtime in stamp:
            if not _mtime:
                continue
            try:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for key in ("cities", "counties"):
                for item in payload.get(key) or []:
                    name = _short_name(item.get("name_zh") or item.get("name"))
                    if not name:
                        continue
                    is_county = key == "counties" or bool(item.get("county"))
                    record = {
                        "name": name,
                        "province": _clean(item.get("province")),
                        "city": _short_name(item.get("city")) or (name if not is_county else ""),
                        "county": _short_name(item.get("county")) or (name if is_county else ""),
                        "adcode": _clean(item.get("adcode")),
                        "lat": _float(item.get("lat")),
                        "lon": _float(item.get("lon")),
                        "source": "scope",
                    }
                    records.setdefault(name, record)
        _SCOPE_CACHE, _SCOPE_STAMP = records, stamp
        return records


def _open_cache():
    path = Path(CACHE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS geocode_cache ("
        "name TEXT PRIMARY KEY, adcode TEXT, lat REAL, lon REAL, "
        "province TEXT, city TEXT, county TEXT, cached_at INTEGER NOT NULL)"
    )
    return conn


def _read_cached(name: str):
    try:
        with _SQL_LOCK, closing(_open_cache()) as conn:
            row = conn.execute(
                "SELECT adcode, lat, lon, province, city, county, cached_at "
                "FROM geocode_cache WHERE name=?", (name,),
            ).fetchone()
    except sqlite3.Error:
        return None
    if not row or int(row[6] or 0) < int(time.time()) - CACHE_TTL_SECONDS:
        return None
    return {
        "name": name, "adcode": row[0] or "", "lat": _float(row[1]),
        "lon": _float(row[2]), "province": row[3] or "",
        "city": row[4] or "", "county": row[5] or "", "source": "amap",
    }


def _write_cached(record: dict) -> None:
    try:
        with _SQL_LOCK, closing(_open_cache()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO geocode_cache "
                    "(name,adcode,lat,lon,province,city,county,cached_at) "
                    "VALUES (?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "adcode=excluded.adcode, lat=excluded.lat, lon=excluded.lon, "
                    "province=excluded.province, city=excluded.city, "
                    "county=excluded.county, cached_at=excluded.cached_at",
                    (record["name"], record.get("adcode", ""), record.get("lat"),
                     record.get("lon"), record.get("province", ""),
                     record.get("city", ""), record.get("county", ""), int(time.time())),
                )
    except sqlite3.Error:
        # 缓存失败不影响在线解析，也不伪造结果。
        return


def _resolve_amap(name: str, trace=None):
    cached = _read_cached(name)
    if cached:
        _trace(trace, "amap-cache: 命中")
        return cached
    try:
        from app.tools import amap
    except Exception as exc:
        _trace(trace, "amap: 模块不可用 %s" % type(exc).__name__)
        return None
    if not amap.api_key():
        _trace(trace, "amap: 未配置 AMAP_API_KEY")
        return None
    try:
        row = amap.geocode(name)
    except Exception as exc:
        _trace(trace, "amap: %s: %s" % (type(exc).__name__, str(exc)[:100]))
        return None
    location = _clean(row.get("location"))
    if "," not in location:
        _trace(trace, "amap: 未返回有效经纬度")
        return None
    try:
        lon, lat = (float(x) for x in location.split(",", 1))
    except (TypeError, ValueError):
        _trace(trace, "amap: 经纬度格式异常")
        return None
    province = _short_name(row.get("province"))
    city = _short_name(row.get("city"))
    district = _short_name(row.get("district"))
    short = _short_name(name)
    county = district if district and district != city else ""
    record = {
        "name": short, "province": province, "city": city, "county": county,
        "adcode": _clean(row.get("adcode")), "lat": lat, "lon": lon,
        "source": "amap",
    }
    _write_cached(record)
    _trace(trace, "amap: 解析成功")
    return record


def _geo_index():
    """懒加载 GeoNames；优先使用已构建缓存。"""
    global _GEO_INDEX
    if _GEO_INDEX is not None:
        return _GEO_INDEX
    with _GEO_INDEX_LOCK:
        if _GEO_INDEX is not None:
            return _GEO_INDEX
        try:
            from app.rag.indexing.geo import GeoIndex
            cache = Path(rag_cfg.PROCESSED) / "geo_index.json"
            raw = Path(rag_cfg.GEONAMES_CN)
            if cache.exists():
                _GEO_INDEX = GeoIndex.load(cache)
            elif raw.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                _GEO_INDEX = GeoIndex.build(raw, cache_path=cache)
            else:
                _GEO_INDEX = False
        except Exception:
            _GEO_INDEX = False
        return _GEO_INDEX


def _resolve_geonames(name: str, trace=None):
    index = _geo_index()
    if not index:
        _trace(trace, "geonames: geo_index.json 与 CN.zip 均不可用")
        return None
    short = _short_name(name)
    canonical = index.alias2canon.get(short) or short
    candidates = [short, canonical]
    for suffix in _ADMIN_SUFFIXES:
        if short.endswith(suffix) and len(short) > len(suffix) + 1:
            candidates.append(short[:-len(suffix)])
    rec = next((index.name2rec.get(candidate) for candidate in candidates
                if index.name2rec.get(candidate)), None)
    if not rec:
        _trace(trace, "geonames: 未命中")
        return None
    _trace(trace, "geonames: 命中")
    city = _short_name(rec.get("city"))
    county = _short_name(rec.get("county"))
    if rec.get("level") == "county" and not county:
        county = short
    if rec.get("level") == "city" and not city:
        city = short
    return {
        "name": short, "province": _short_name(rec.get("prov")),
        "city": city, "county": county, "adcode": "",
        "lat": _float(rec.get("lat")), "lon": _float(rec.get("lon")),
        "source": "geonames",
    }


def resolve_place(name: str, trace: list | None = None):
    """任意中文行政地名 -> 规范化地点记录；全部来源失败返回 None。"""
    cleaned = _clean(name)
    if not cleaned:
        _trace(trace, "input: 空地名")
        return None
    scope = _load_scope()
    place = scope.get(_short_name(cleaned))
    if place:
        _trace(trace, "scope: 命中")
        return dict(place)
    place = _resolve_amap(cleaned, trace)
    if place:
        return place
    return _resolve_geonames(cleaned, trace)


def reset_caches() -> None:
    """测试与数据重建后清空进程内缓存。"""
    global _SCOPE_CACHE, _SCOPE_STAMP, _GEO_INDEX
    with _SQL_LOCK:
        _SCOPE_CACHE = None
        _SCOPE_STAMP = None
    with _GEO_INDEX_LOCK:
        _GEO_INDEX = None
