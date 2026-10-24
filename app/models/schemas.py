# -*- coding: utf-8 -*-
"""Agent 层数据模型。

对应 PRD v1.2 第 5.4 节的数据模型表：
    TravelRequest / IntentResult / ToolResult / SourceChunk / ItineraryDay / Trace

约定：这里的模型只描述**结构**，不承载任何外部事实。
实时数值（天气、票价、余票）一律放在 ToolResult 里，并且必须带供应商与时间戳。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def now_iso() -> str:
    """当前时间（带时区），用于 fetched_at / expires_at。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dump(model: Any) -> dict:
    """兼容 pydantic v1 / v2 的导出。

    必须用 mode="json"：否则枚举会以自定义类型进入 LangGraph 的 checkpoint 序列化，
    触发 "Deserializing unregistered type" 告警，落盘轨迹也不是纯 JSON。
    """
    if hasattr(model, "model_dump"):
        try:
            return model.model_dump(mode="json")
        except TypeError:
            return model.model_dump()
    return model.dict()


def validate(model_cls, data: dict):
    """兼容 pydantic v1 / v2 的校验入口。"""
    if hasattr(model_cls, "model_validate"):
        return model_cls.model_validate(data)
    return model_cls.parse_obj(data)


class Intent(str, Enum):
    """PRD 第 3.1 节的七类意图。"""

    DESTINATION = "INT-01"
    ITINERARY = "INT-02"
    TRANSPORT = "INT-03"
    HOTEL = "INT-04"
    FOOD = "INT-05"
    WEATHER = "INT-06"
    CHITCHAT = "INT-07"


INTENT_LABEL = {
    Intent.DESTINATION: "目的地信息查询",
    Intent.ITINERARY: "行程规划",
    Intent.TRANSPORT: "交通查询",
    Intent.HOTEL: "住宿查询",
    Intent.FOOD: "美食推荐",
    Intent.WEATHER: "天气查询",
    Intent.CHITCHAT: "闲聊与兜底",
}


class ToolStatus(str, Enum):
    """工具调用结果状态。

    UNAVAILABLE 是一等公民：拿不到真实数据时必须如实上报，
    不允许用过期缓存或编造值顶替（PRD 第 6.4 节边界管控）。
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class SlotBundle(BaseModel):
    """统一槽位模型（PRD 第 3.1 节）。"""

    destination: str | None = None
    start_city: str | None = None
    target_city: str | None = None
    origin: str | None = None
    destination_place: str | None = None
    date: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = Field(None, ge=1, le=14)
    people: int | None = Field(None, ge=1)
    budget: int | None = Field(None, ge=0)
    category: str | None = None
    entity: str | None = None
    area: str | None = None
    preferences: list[str] = Field(default_factory=list)


class LLMIntentPayload(BaseModel):
    """让大模型返回的最小结构（槽位先收成自由 dict，再逐字段过滤）。"""

    intent: str
    confidence: float = 0.0
    slots: dict = Field(default_factory=dict)


class LLMTaskPayload(BaseModel):
    """复杂问法拆出的单个可执行任务。"""

    intent: str
    query: str = ""
    slots: dict = Field(default_factory=dict)


class LLMQueryPlanPayload(BaseModel):
    """语言模型对口语化、碎片化、多意图问题生成的结构化计划。"""

    normalized_query: str = ""
    confidence: float = 0.0
    tasks: list[LLMTaskPayload] = Field(default_factory=list)


class IntentResult(BaseModel):
    """意图识别与槽位抽取结果（PRD FR-01）。"""

    intent: Intent
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    slots: SlotBundle = Field(default_factory=SlotBundle)
    missing: list[str] = Field(default_factory=list)
    source: str = "rule"


class ToolResult(BaseModel):
    """工具调用结果（PRD 第 5.4 节）。"""

    provider: str
    status: ToolStatus = ToolStatus.UNAVAILABLE
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    fetched_at: str = Field(default_factory=now_iso)
    expires_at: str | None = None
    error_code: str = ""
    latency_ms: int = 0


class SourceChunk(BaseModel):
    """一条证据。知识证据与工具证据统一成同一形状，便于逐句绑定。"""

    source_id: str
    title: str = ""
    url: str = ""
    text: str = ""
    score: float = 0.0
    updated_at: str = ""
    source_type: str = "public"
    city: str = ""
    kind: str = "knowledge"


class ItineraryDay(BaseModel):
    """行程中的一天（PRD 第 4.2 节）。"""

    date: str = ""
    periods: list[dict] = Field(default_factory=list)
    cost_min: float | None = None
    cost_max: float | None = None
    warnings: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    """一个节点的执行记录（可观测性要求：node / latency / status）。"""

    node: str
    status: str = "ok"
    latency_ms: int = 0
    detail: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class AgentReply(BaseModel):
    """对外返回的完整结果。"""

    thread_id: str = ""
    user_id: str = ""
    trace_id: str = ""
    query: str = ""
    normalized_query: str = ""
    intent: str = ""
    intent_label: str = ""
    confidence: float = 0.0
    slots: dict = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    answer: str = ""
    citations: list[dict] = Field(default_factory=list)
    tool_refs: list[dict] = Field(default_factory=list)
    degraded: bool = False
    no_evidence: bool = False
    evidence_binding_rate: float = 0.0
    plan: dict = Field(default_factory=dict)
    trace: list[dict] = Field(default_factory=list)
    profile: dict = Field(default_factory=dict)
    route_source: str = ""
    urgent: bool = False
    emotion: float | None = None


class ChatRequest(BaseModel):
    """对话请求（PRD 第 5.3 节 POST /api/v1/chat）。"""

    message: str = Field(..., min_length=1, description="用户输入")
    user_id: str | None = Field(None, description="用户 ID；长期记忆按它归属")
    thread_id: str | None = Field(None, description="会话 ID；不传则新建")
    stream: bool = Field(True, description="是否使用 SSE")
    thinking: bool = Field(False, description="深度思考：要求更详细的规划与理由")
    smart_search: bool = Field(True, description="智能搜索：是否启用知识库检索")


class UserProfile(BaseModel):
    """跨会话的长期偏好（只存用户偏好，不存事实证据）。"""

    preferences: list[str] = Field(default_factory=list)
    budget: int | None = None
    people: int | None = None
    updated_at: str = ""


class FeedbackRequest(BaseModel):
    """用户反馈（PRD 第 5.3 节 POST /api/v1/feedback）。"""

    trace_id: str = Field(..., min_length=1)
    score: int = Field(..., ge=-1, le=1, description="1 点赞，-1 点踩")
    reason: str = ""
