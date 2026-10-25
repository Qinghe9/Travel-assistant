# -*- coding: utf-8 -*-
"""规则式槽位抽取（无密钥时的降级路径，也用于给大模型结果兜底）。

重要：这是降级路径而不是 mock —— 它只从用户这句话里抽信息，
不会凭空生成任何事实。抽不到的字段一律留空，由 route 决定是否追问。
"""

from __future__ import annotations

import json
import re

from app.models.schemas import Intent

# 基础城市表：直辖市 + 省会 + 常见旅游城市
BASE_CITIES = [
    "北京", "上海", "天津", "重庆", "香港", "澳门",
    "石家庄", "太原", "呼和浩特", "沈阳", "长春", "哈尔滨", "南京", "杭州", "合肥",
    "福州", "南昌", "济南", "郑州", "武汉", "长沙", "广州", "南宁", "海口", "成都",
    "贵阳", "昆明", "拉萨", "西安", "兰州", "西宁", "银川", "乌鲁木齐", "台北",
    "深圳", "苏州", "无锡", "宁波", "厦门", "青岛", "大连", "珠海", "三亚", "桂林",
    "丽江", "大理", "香格里拉", "西双版纳", "张家界", "黄山", "泰山", "洛阳", "开封",
    "扬州", "绍兴", "泉州", "佛山", "东莞", "温州", "常州", "南通", "徐州", "烟台",
    "威海", "秦皇岛", "承德", "大同", "平遥", "敦煌", "吐鲁番", "喀什", "呼伦贝尔",
    "满洲里", "景德镇", "婺源", "凤凰", "九寨沟", "峨眉山", "曲阜",
    "安顺", "遵义", "凯里", "都匀", "赤水", "兴义", "六盘水", "铜仁",
]

PROVINCES = {
    "河北": "石家庄", "山西": "太原", "辽宁": "沈阳", "吉林": "长春",
    "黑龙江": "哈尔滨", "江苏": "南京", "浙江": "杭州", "安徽": "合肥",
    "福建": "福州", "江西": "南昌", "山东": "济南", "河南": "郑州",
    "湖北": "武汉", "湖南": "长沙", "广东": "广州", "海南": "海口",
    "四川": "成都", "贵州": "贵阳", "云南": "昆明", "陕西": "西安",
    "甘肃": "兰州", "青海": "西宁", "台湾": "台北",
    "内蒙古": "呼和浩特", "广西": "南宁", "西藏": "拉萨",
    "宁夏": "银川", "新疆": "乌鲁木齐",
}

_CITY_CACHE = None


def cities() -> list:
    """城市表：内置表 +（若存在）RAG 层的 scope_cities.json。

    注意：RAG 层的 data/scope_cities.json 被 .gitignore 排除，换台机器可能不存在；
    这里保留内置表，保证 Agent 在没有该文件时仍然可用（不抛异常、不静默失效）。
    """
    global _CITY_CACHE
    if _CITY_CACHE is not None:
        return _CITY_CACHE
    names = set(BASE_CITIES)
    try:
        from app.rag import config as rag_cfg
        if rag_cfg.SCOPE_FILE.exists():
            data = json.loads(rag_cfg.SCOPE_FILE.read_text(encoding="utf-8"))
            names.update(c["name_zh"] for c in data.get("cities", []) if c.get("name_zh"))
    except Exception:
        pass
    _CITY_CACHE = sorted(names, key=len, reverse=True)
    return _CITY_CACHE


def detect_city(text: str):
    """从文本里识别城市名；命中多个时取最先出现的最长名。"""
    if not text:
        return None
    best = None
    for name in cities():
        pos = text.find(name)
        if pos >= 0 and (best is None or pos < best[0] or (pos == best[0] and len(name) > len(best[1]))):
            best = (pos, name)
    return best[1] if best else None


def detect_province(text: str):
    """识别省级行政区；用于避免把“贵州”等省名直接当城市查实时数据。"""
    q = text or ""
    matches = [(q.find(name), name) for name in PROVINCES if name in q]
    matches = [item for item in matches if item[0] >= 0]
    return min(matches)[1] if matches else None


_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}
_CN = "一二三四五六七八九十两"
_NUM = "[0-9" + _CN + "]{1,3}"


def _to_int(raw: str):
    """把阿拉伯数字或中文数字转成 int；无法识别返回 None。"""
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    if "十" in raw:
        head, _, tail = raw.partition("十")
        tens = _CN_DIGIT.get(head, 1) if head else 1
        ones = _CN_DIGIT.get(tail, 0) if tail else 0
        return tens * 10 + ones
    total = 0
    for ch in raw:
        if ch not in _CN_DIGIT:
            return None
        total = total * 10 + _CN_DIGIT[ch]
    return total


def parse_days(text: str):
    # (?<!第) 很关键：多轮里“第二天改成亲子活动”是修改某一天，
    # 不是把行程改成 2 天；没有这个负向断言会把继承来的天数覆盖掉。
    m = re.search(r"(?<!第)(" + _NUM + r")\s*天", text) or re.search(r"(?<!第)(" + _NUM + r")日游", text)
    if not m:
        return None
    days = _to_int(m.group(1))
    return days if days and 1 <= days <= 14 else None


def parse_people(text: str):
    m = re.search("(" + _NUM + r")\s*(?:个?人|位)", text)
    return _to_int(m.group(1)) if m else None


def parse_budget(text: str):
    m = re.search(r"(?:预算|花费|费用|大约|大概)\D{0,4}([0-9]{2,7})", text)
    if m:
        return int(m.group(1))
    m = re.search(r"([0-9]{2,7})\s*元", text)
    return int(m.group(1)) if m else None


_REL_DAY = {
    "大后天": 3, "后天": 2, "明天": 1, "明日": 1,
    "今天": 0, "今日": 0, "当天": 0, "当日": 0,
}


def parse_date(text: str):
    """解析相对日期与显式日期，返回 ISO 字符串；解析不出来返回 None（不猜）。"""
    import datetime as _dt
    today = _dt.date.today()
    for word, offset in _REL_DAY.items():
        if word in text:
            return (today + _dt.timedelta(days=offset)).isoformat()
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        for year in (today.year, today.year + 1):
            try:
                target = _dt.date(year, month, day)
            except ValueError:
                return None
            if target >= today:
                return target.isoformat()
    return None


_RANGE_N = re.compile(r"(?:最近|未来|接下来|今后|这|后面)\s*([0-9" + _CN + r"]{1,3})\s*天")
_RANGE_DAY = re.compile(r"(今天|明天|后天)\s*(?:到|至|~|-|—)\s*(今天|明天|后天)")
_RANGE_MD = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*(?:到|至|~|-|—)\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?")
_DAY_OFFSET = {"今天": 0, "明天": 1, "明日": 1, "后天": 2}
_RANGE_WEEK_REL = re.compile(r"(?:最近|未来|接下来|今后)\s*(?:一|1|两|2)\s*(?:个)?\s*(?:星期|周|礼拜)")
_RANGE_WEEK_THIS = re.compile(r"(?:本|这)\s*(?:一|1)?\s*(?:个)?\s*(?:星期|周|礼拜)")
_RANGE_WEEK_NEXT = re.compile(r"下\s*(?:一|1)?\s*(?:个)?\s*(?:星期|周|礼拜)")
_RANGE_WEEK_N = re.compile(r"([0-9一二两]{1,2})\s*(?:个)?\s*(?:星期|周|礼拜)")
_RANGE_BEFORE_MD = re.compile(
    r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?\s*之前")
_RANGE_BEFORE_DAY = re.compile(r"(\d{1,2})\s*[日号]\s*之前")


def _range_of(start, end):
    return {"start_date": start.isoformat(), "end_date": end.isoformat(),
            "days": (end - start).days + 1}


def parse_date_range(text: str):
    """把「最近三天」「今天到后天」「10月20日到22日」解析成区间。

    返回 {"start_date","end_date","days"} 或 None。
    语义约定：「最近/未来/接下来 N 天」按**今天起 N 天**理解。
    """
    import datetime as _dt
    q = text or ""
    today = _dt.date.today()

    m = _RANGE_N.search(q)
    if m:
        days = _to_int(m.group(1))
        if days and 1 <= days <= 14:
            return _range_of(today, today + _dt.timedelta(days=days - 1))

    if _RANGE_WEEK_REL.search(q):
        return _range_of(today, today + _dt.timedelta(days=6))

    if _RANGE_WEEK_THIS.search(q):
        # 本周：今天到本周日（周一为一周起点）
        return _range_of(today, today + _dt.timedelta(days=6 - today.weekday()))

    if _RANGE_WEEK_NEXT.search(q):
        start = today + _dt.timedelta(days=7 - today.weekday())
        return _range_of(start, start + _dt.timedelta(days=6))

    m = _RANGE_WEEK_N.search(q)
    if m:
        weeks = _to_int(m.group(1))
        if weeks and 1 <= weeks <= 2:
            return _range_of(today, today + _dt.timedelta(days=weeks * 7 - 1))

    # “10月31日之前/31号之前”按今天至目标日前一天理解（不包含目标日）。
    m = _RANGE_BEFORE_MD.search(q)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = today.year
        try:
            target = _dt.date(year, month, day)
            if target < today:
                target = _dt.date(year + 1, month, day)
        except ValueError:
            return None
        end = target - _dt.timedelta(days=1)
        if end >= today:
            return _range_of(today, end)

    m = _RANGE_BEFORE_DAY.search(q)
    if m:
        day = int(m.group(1))
        try:
            target = _dt.date(today.year, today.month, day)
            if target < today:
                next_month = today.replace(day=28) + _dt.timedelta(days=4)
                target = next_month.replace(day=day)
        except ValueError:
            return None
        end = target - _dt.timedelta(days=1)
        if end >= today:
            return _range_of(today, end)

    m = _RANGE_DAY.search(q)
    if m:
        start = today + _dt.timedelta(days=_DAY_OFFSET.get(m.group(1), 0))
        end = today + _dt.timedelta(days=_DAY_OFFSET.get(m.group(2), 0))
        if end >= start:
            return _range_of(start, end)

    m = _RANGE_MD.search(q)
    if m:
        try:
            start = _dt.date(today.year, int(m.group(1)), int(m.group(2)))
            end = _dt.date(today.year, int(m.group(3)), int(m.group(4)))
        except ValueError:
            return None
        if end < start:                      # 跨年
            try:
                start = _dt.date(today.year + 1, start.month, start.day)
                end = _dt.date(today.year + 1, end.month, end.day)
            except ValueError:
                return None
        if start < today:
            try:
                start = _dt.date(today.year + 1, start.month, start.day)
                end = _dt.date(today.year + 1, end.month, end.day)
            except ValueError:
                return None
        if end >= start:
            return {"start_date": start.isoformat(), "end_date": end.isoformat(),
                    "days": (end - start).days + 1}
    return None


def parse_route_places(text: str):
    """解析「从 A 到 B」/「A 到 B」的原始出发地、到达地（可为城市或具体地点）。"""
    q = text or ""
    m = re.search(r"从\s*([\u4e00-\u9fffA-Za-z0-9]{2,12}?)\s*(?:到|去|飞往|前往)\s*"
                  r"([\u4e00-\u9fffA-Za-z0-9]{2,12}?)(?=怎么|如何|坐|的|有|多少|多久|$)", q)
    if not m:
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9]{2,12}?)\s*到\s*"
                      r"([\u4e00-\u9fffA-Za-z0-9]{2,10}?)(?=[的怎有高火动飞机地]|$)", q)
    if not m:
        # 复合对话里常见“我想去北京，怎么从广州出发”——后半句省略终点。
        # 这里只抽出出发地，终点由多任务共享上下文补齐。
        m = re.search(r"从\s*([\u4e00-\u9fffA-Za-z0-9]{2,12}?)\s*"
                      r"(?:出发|启程|动身)(?=[，,？?。]|$)", q)
        if m:
            return m.group(1).strip(), None
    if not m:
        return None, None
    return m.group(1).strip(), m.group(2).strip()


def parse_route(text: str):
    """解析出发/到达城市（能识别城市时）。"""
    origin, dest = parse_route_places(text)
    return detect_city(origin), detect_city(dest)


_CATEGORY_WORDS = [
    ("food", ("美食", "小吃", "吃", "餐厅", "火锅", "菜")),
    ("hotel", ("住宿", "酒店", "民宿", "宾馆", "旅馆", "住哪")),
    ("transport", ("交通", "高铁", "火车", "航班", "飞机", "机票", "动车", "车站", "机场")),
    ("attraction", ("景点", "景区", "好玩", "必去", "游玩", "博物馆", "公园", "怎么玩")),
]

_PREFERENCE_WORDS = {
    "亲子": ("亲子", "孩子", "小朋友", "带娃", "儿童"),
    "历史文化": ("历史", "文化", "古迹", "古城", "博物馆", "人文"),
    "自然风光": ("自然", "风光", "山水", "徒步", "户外"),
    "购物": ("购物", "商圈", "买东西"),
    "摄影": ("摄影", "拍照", "打卡"),
}


def parse_category(text: str):
    for cat, words in _CATEGORY_WORDS:
        if any(w in text for w in words):
            return cat
    return None


def parse_preferences(text: str) -> list:
    return [name for name, words in _PREFERENCE_WORDS.items() if any(w in text for w in words)]


_ENTITY_SUFFIX = ("景区", "公园", "博物馆", "纪念馆", "故居", "古镇", "寺", "庙", "塔", "湖", "山",
                  "广场", "步行街", "酒店", "宾馆", "饭店", "餐厅", "机场", "车站", "祠", "园", "桥")
_GENERIC = {"景点", "景区", "美食", "酒店", "住宿", "交通", "开放时间", "门票", "价格", "地址"}


def parse_entity(text: str, city: str = None):
    """识别用户显式提到的具体地点/酒店名；宁缺毋滥。"""
    q = text or ""
    if city:
        q = q.replace(city, "", 1)
    quoted = re.search("[\u201c\"\u300a]([^\u201d\"\u300b]{2,24})[\u201d\"\u300b]", q)
    if quoted and quoted.group(1) not in _GENERIC:
        return quoted.group(1)
    intent = re.search(r"^([\u4e00-\u9fffA-Za-z0-9·]{2,24}?)(?:怎么玩|怎么样|值得去吗|值得吗|好玩吗|介绍一下)", q)
    if intent and intent.group(1) not in _GENERIC:
        return intent.group(1)
    for suffix in _ENTITY_SUFFIX:
        m = re.search(r"([\u4e00-\u9fffA-Za-z0-9·]{1,20}" + re.escape(suffix) + ")", q)
        if m and m.group(1) not in _GENERIC:
            candidate = re.sub(r"^(请问|介绍一下|推荐|去|到)", "", m.group(1))
            if candidate and candidate not in _GENERIC:
                return candidate
    return None


_INTENT_RULES = [
    (Intent.TRANSPORT, ("高铁", "火车", "动车", "航班", "飞机", "机票",
                        "怎么去", "怎么从", "怎么走", "怎么坐车", "怎么坐",
                        "地铁", "公交", "轻轨", "城际", "换乘")),
    (Intent.WEATHER, ("天气", "气温", "温度", "下雨", "下雪", "冷不冷", "热不热")),
    (Intent.HOTEL, ("酒店", "住宿", "民宿", "宾馆", "旅馆", "住哪", "住哪里",
                    "哪里住", "在哪住", "哪儿住", "住在哪")),
    (Intent.FOOD, ("美食", "小吃", "必吃", "吃什么", "好吃", "餐厅", "火锅")),
    (Intent.ITINERARY, ("规划", "行程", "安排", "路线", "几天", "几日游")),
    (Intent.DESTINATION, ("景点", "好玩", "必去", "有什么", "玩什么", "怎么玩", "值得去", "介绍")),
]

_CHITCHAT_WORDS = ("你好", "您好", "谢谢", "多谢", "在吗", "你是谁", "你能做什么", "再见", "哈哈")

_DAY_REF = re.compile(r"第\s*[" + _CN + r"0-9]{1,3}\s*天")


def rule_intent(text: str) -> Intent:
    """规则式意图判定（降级路径）。"""
    q = text or ""
    # 明确指向“第 N 天”的，是对已有行程的修改请求，按行程规划处理
    if _DAY_REF.search(q):
        return Intent.ITINERARY
    for intent, words in _INTENT_RULES:
        if any(w in q for w in words):
            return intent
    # “我想去北京玩/去北京旅游”没有“景点/好玩”字样，但明确是目的地游玩。
    if re.search(r"(?:去|到)[\u4e00-\u9fff]{2,8}(?:玩|旅游|旅行)", q):
        return Intent.DESTINATION
    return Intent.CHITCHAT


SLOT_FIELDS = ("destination", "province", "start_city", "target_city", "origin", "destination_place",
               "date", "start_date", "end_date", "days", "people", "budget",
               "category", "entity", "area", "preferences")


def parse_area(text: str, city: str = None):
    """抽取区域/商圈：天河区、珠江新城、西湖附近。"""
    q = text or ""
    if city:
        q = q.replace(city, "", 1)
    m = re.search(r"([\u4e00-\u9fff]{2,12}?(?:区|县|镇|街道|商圈|开发区|新区))", q)
    if m:
        return m.group(1).strip()
    m = re.search(r"([\u4e00-\u9fff]{2,12}?)(?:附近|周边|一带|周围)", q)
    if m:
        cand = re.sub(r"^(请问|想找|找|在|去)", "", m.group(1).strip())
        if cand and cand not in ("酒店", "住宿", "宾馆", "民宿", "这里", "那边"):
            return cand
    return None


def rule_extract(text: str) -> dict:
    """规则式槽位抽取，返回只含非空字段的 dict。"""
    text = text or ""
    out = {}
    start, target = parse_route(text)
    origin_raw, dest_raw = parse_route_places(text)
    city = detect_city(text)
    if origin_raw:
        out["origin"] = origin_raw
    if dest_raw:
        out["destination_place"] = dest_raw
    if start:
        out["start_city"] = start
    if target:
        out["target_city"] = target
    # 单独的“从广州出发”只表示出发地，不能把广州误当目的地。
    destination = target or (city if not (origin_raw and not dest_raw) else None)
    if destination:
        out["destination"] = destination
    area = parse_area(text, destination)
    if area:
        out["area"] = area
    date_range = parse_date_range(text)
    if date_range:
        out.update(date_range)
        out["date"] = date_range["start_date"]
    else:
        date = parse_date(text)
        if date:
            out["date"] = date
    days = parse_days(text)
    if days:
        out["days"] = days
    people = parse_people(text)
    if people:
        out["people"] = people
    budget = parse_budget(text)
    if budget is not None:
        out["budget"] = budget
    category = parse_category(text)
    if category:
        out["category"] = category
    entity = parse_entity(text, destination)
    if entity:
        out["entity"] = entity
    prefs = parse_preferences(text)
    if prefs:
        out["preferences"] = prefs
    return out


def merge_slots(previous: dict, incoming: dict, new_city: str = None) -> dict:
    """合并槽位：本轮识别到的覆盖历史继承值；preferences 取并集。

    换城市视为换话题：清掉继承来的 category/entity。否则
    "成都美食" -> "杭州西湖怎么玩" 会把 food 过滤带到杭州
    （与 RAG 层 dialogue.prepare_turn 里踩过的坑同源）。
    """
    slots = dict(previous or {})
    old_city = slots.get("destination")
    if new_city and old_city and old_city != new_city:
        slots.pop("category", None)
        slots.pop("entity", None)
    for key in SLOT_FIELDS:
        value = (incoming or {}).get(key)
        if value in (None, "", [], {}):
            continue
        if key == "preferences":
            slots[key] = list(dict.fromkeys(list(slots.get(key) or []) + list(value)))
        else:
            slots[key] = value
    return slots


TRANSIENT_SLOTS = ("category", "entity", "area")
_REFERENCE_WORDS = (
    "它", "这个", "该", "那里", "那边", "上面", "刚才", "前面",
    "门票", "开放时间", "营业时间", "距离", "多远", "怎么去", "附近",
    "还有", "另外", "再", "呢", "换成", "改成",
)


def scope_previous_slots(previous: dict, text: str, incoming: dict, intent: Intent,
                         last_intent: str = "") -> dict:
    """隔离单轮实体，避免上一轮“酒店”污染本轮景点或 RAG 搜索。"""
    out = dict(previous or {})
    same_follow_up = (last_intent == intent.value
                      and any(word in (text or "") for word in _REFERENCE_WORDS))
    if same_follow_up:
        return out
    for key in TRANSIENT_SLOTS:
        if (incoming or {}).get(key) in (None, "", [], {}):
            out.pop(key, None)
    return out


def normalize_text(text: str) -> str:
    """归一化用户输入：全角转半角、压缩空白。"""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch == "\u3000":
            out.append(" ")
        else:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


_SUBQUERY_SPLIT = re.compile(
    r"[？?！!；;\n]+"
    r"|(?:，|,)\s*(?=(?:还有|另外|顺便|并且|同时|然后|再|应该|那))"
)


def split_subqueries(text: str) -> list:
    """把一句话里的多个问题拆成独立子查询；普通单问句保持原样。"""
    parts = [normalize_text(x).strip(" ，,。") for x in _SUBQUERY_SPLIT.split(text or "")]
    parts = [x for x in parts if x]
    return parts if len(parts) > 1 else [normalize_text(text)]
