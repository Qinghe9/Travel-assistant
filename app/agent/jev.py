# -*- coding: utf-8 -*-
"""JevK5 结构化决策客户端（TypeSafe Jev）。

它只做**判断**（choice / noul / score），不生成回答：
- choice：意图路由，返回 choice + confidence + 全部选项概率；
- noul：是否着急，返回 noul 概率；
- score：情绪强度，返回 score 与各类概率。

两种接入方式（同一把 appKey）：
- gateway：POST https://route.showapi.com/3627-1?appKey=...
- native ：POST https://jev.showapi.com/v1/systemone（Authorization: Bearer ...）

失败/超时/低置信度一律回退到主 LLM 或规则式，不阻断对话。
"""

from __future__ import annotations

import hashlib
import time

from app.agent import config as cfg
from app.models.schemas import Intent

GATEWAY_URL = "https://route.showapi.com/3627-1"
NATIVE_URL = "https://jev.showapi.com/v1/systemone"

# 七个选项的键直接用 PRD 的意图编号，便于路由
INTENT_CRITERIA = {
    "INT-01": "目的地信息查询：问某地有什么好玩、有哪些景点、介绍某个城市或景点",
    "INT-02": "行程规划：要求安排几天行程、路线规划、逐日计划、怎么玩",
    "INT-03": "交通查询：从 A 到 B 怎么走、高铁/火车/航班/机票/自驾路线",
    "INT-04": "住宿查询：酒店、民宿、宾馆、住哪里、住宿推荐",
    "INT-05": "美食推荐：吃什么、特色小吃、必吃、餐厅",
    "INT-06": "天气查询：天气、气温、下雨、降温、未来几天天气",
    "INT-07": "闲聊、问候、感谢、与旅行无关，或信息不足无法判断",
}

_CACHE = {}
_CACHE_TTL = 300


class JevError(RuntimeError):
    pass


def available() -> bool:
    return bool(cfg.JEV_APP_KEY)


def build_questions() -> dict:
    return {
        "route": {
            "type": "choice",
            "instructions": (
                "判断用户最新需求属于哪一类旅游意图。结合历史对话消解省略与指代；"
                "只做分类，不要生成回答。信息不足或与旅行无关时选 INT-07。"
            ),
            "criteria": INTENT_CRITERIA,
        },
        "is_urgent": {
            "type": "noul",
            "instructions": "用户是否表达了时间紧迫性或催促处理的意愿？",
            "criteria": {
                "true": "明确要求尽快、今天、马上或 ASAP 处理",
                "false": "只是正常提问，没有时间压力",
            },
        },
        "frustration": {
            "type": "score",
            "instructions": "用户表达的情绪强度处于哪个程度？",
            "criteria": ["平静陈述", "略有不满或着急", "明显愤怒或强烈催促"],
        },
    }


def compose_state(history: str, query: str) -> str:
    """把最近对话和当前问题拼成决策输入。"""
    history = (history or "").strip()
    if history:
        return "%s\n用户：%s" % (history, query or "")
    return "用户：%s" % (query or "")


def _cache_key(text: str) -> str:
    raw = "%s|%s|%s" % (cfg.JEV_MODEL, text, cfg.JEV_MIN_CONFIDENCE)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def decide(text: str, timeout: float = None) -> dict:
    """调用 Jev 做结构化判断；任何失败抛 JevError。"""
    if not available():
        raise JevError("未配置 JEV_APP_KEY")

    key = _cache_key(text)
    hit = _CACHE.get(key)
    if hit and hit[0] > time.time():
        return hit[1]

    try:
        import httpx
    except ImportError as exc:
        raise JevError("缺少 httpx 依赖") from exc

    payload = {"model": cfg.JEV_MODEL, "state": text, "questions": build_questions()}
    timeout = timeout or cfg.JEV_TIMEOUT
    try:
        if (cfg.JEV_MODE or "gateway").lower() == "native":
            resp = httpx.post(NATIVE_URL, json=payload, timeout=timeout,
                              headers={"Authorization": "Bearer %s" % cfg.JEV_APP_KEY})
        else:
            resp = httpx.post(GATEWAY_URL, json=payload, timeout=timeout,
                              params={"appKey": cfg.JEV_APP_KEY})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise JevError("调用失败：%s: %s" % (type(exc).__name__, str(exc)[:160])) from exc

    answers = data.get("answers")
    if not isinstance(answers, dict):
        raise JevError("返回格式异常：缺少 answers")

    route = answers.get("route") or {}
    urgent_answer = answers.get("is_urgent") or {}
    emotion_answer = answers.get("frustration") or {}

    noul = urgent_answer.get("noul")
    urgent = None if noul is None else (float(noul) < 0.5)
    emotion = emotion_answer.get("score")

    result = {
        "model": data.get("model", cfg.JEV_MODEL),
        "choice": route.get("choice"),
        "confidence": float(route.get("confidence") or 0.0),
        "probabilities": route.get("probabilities") or {},
        "urgent": urgent,
        "urgent_confidence": float(urgent_answer.get("confidence") or 0.0),
        "emotion": None if emotion is None else float(emotion),
        "emotion_confidence": float(emotion_answer.get("confidence") or 0.0),
        "latency_ms": data.get("latency_ms"),
        "usage": data.get("usage") or {},
    }
    _CACHE[key] = (time.time() + _CACHE_TTL, result)
    return result


def confident_intent(result: dict):
    """置信度达标时返回 Intent，否则 None（交给主 LLM/规则）。"""
    if not result:
        return None
    choice = result.get("choice")
    confidence = float(result.get("confidence") or 0.0)
    if not choice or confidence < cfg.JEV_MIN_CONFIDENCE:
        return None
    try:
        return Intent(choice)
    except ValueError:
        return None
