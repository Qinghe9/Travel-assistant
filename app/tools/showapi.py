# -*- coding: utf-8 -*-
"""万维易源 ShowAPI 免费接口：节假日/调休、城市空气质量。

统一网关：POST https://route.showapi.com/{apiCode}-{point}?appKey=...
返回统一封装在 showapi_res_body 中。

选这两个接口的原因：
- 节假日/调休：影响人流、闭馆、交通与预算，属于行程规划的硬约束；
- 空气质量：影响户外/亲子行程与健康提示；
- 两者都是免费接口，且与现有高德/天气形成互补。

注意：ShowAPI 没有 12306 车次/余票接口；火车票相关只做票据 OCR，不做时刻表。
"""

from __future__ import annotations

import os

from app.tools.base import Provider, make_ok, make_unavailable

BASE = "https://route.showapi.com"


class ShowAPIError(RuntimeError):
    pass


def app_key() -> str:
    # ShowAPI 的 appKey 与 JevK5 共用同一个平台 Key；优先独立配置
    return (os.environ.get("SHOWAPI_APP_KEY")
            or os.environ.get("JEV_APP_KEY") or "").strip()


def _call(path: str, form: dict, timeout: float = 5.0) -> dict:
    if not app_key():
        raise ShowAPIError("未配置 SHOWAPI_APP_KEY")
    try:
        import httpx
    except ImportError as exc:
        raise ShowAPIError("缺少 httpx 依赖") from exc
    print("[showapi] request path=%s form=%s" % (path, form), flush=True)
    try:
        resp = httpx.post(BASE + path, params={"appKey": app_key()},
                          data=form or {}, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise ShowAPIError("调用失败：%s: %s" % (type(exc).__name__, str(exc)[:160])) from exc

    if data.get("showapi_res_code") != 0:
        raise ShowAPIError("%s（code=%s）" % (data.get("showapi_res_error") or "调用失败",
                                              data.get("showapi_res_code")))
    body = data.get("showapi_res_body") or {}
    if body.get("ret_code") not in (0, "0", None):
        raise ShowAPIError("业务错误 ret_code=%s" % body.get("ret_code"))
    return body


class ShowAPIHoliday(Provider):
    """节假日/调休：按日期查，或按年份列假期。"""

    name = "showapi_holiday"
    ttl = 86400
    timeout = 5.0

    def fetch(self, date: str = None, year: str = None):
        if not app_key():
            return make_unavailable(self.name, "未配置 SHOWAPI_APP_KEY，节假日接口暂不可用",
                                    "no_api_key")
        if date:
            day = str(date).replace("-", "")
            body = _call("/894-6", {"day": day, "needDesc": "1"}, timeout=self.timeout)
            holiday = body.get("holiday") or ""
            return make_ok(self.name, {
                "日期": date,
                "星期": body.get("cn") or "",
                # 接口把普通周末也算 holiday=周末，所以用"是否假日"而不是"法定假日"
                "是否假日": bool(holiday),
                "节日": holiday or "工作日",
                "类型码": body.get("type") or "",
                "假期开始": body.get("begin") or "",
                "假期结束": body.get("end") or "",
                "说明": body.get("holiday_remark") or "",
                "数据来源": "万维易源 ShowAPI",
            }, "获取成功", ttl=self.ttl)
        if year:
            body = _call("/894-4", {"year": str(year)}, timeout=self.timeout)
            items = []
            for row in (body.get("data") or [])[:10]:
                items.append("%s：%s 至 %s（%s）" % (
                    row.get("holiday") or "", row.get("begin") or "",
                    row.get("end") or "", row.get("holiday_remark") or ""))
            if not items:
                return make_unavailable(self.name, "该年份没有返回假期数据", "empty_response")
            return make_ok(self.name, {"年份": str(year), "假期": items,
                                       "数据来源": "万维易源 ShowAPI"},
                           "获取成功", ttl=self.ttl)
        return make_unavailable(self.name, "缺少日期或年份", "bad_request")


class ShowAPIAirQuality(Provider):
    """全国城市空气质量（AQI / PM2.5 / 质量等级）。"""

    name = "showapi_air_quality"
    ttl = 1800
    timeout = 5.0

    def fetch(self, city: str = None):
        if not app_key():
            return make_unavailable(self.name, "未配置 SHOWAPI_APP_KEY，空气质量接口暂不可用",
                                    "no_api_key")
        if not city:
            return make_unavailable(self.name, "缺少城市参数", "bad_request")
        body = _call("/104-42", {"area": city}, timeout=self.timeout)
        return make_ok(self.name, {
            "城市": body.get("area") or city,
            "空气质量": body.get("quality") or "",
            "AQI": body.get("aqi") or "",
            "PM2.5": body.get("pm2_5") or "",
            "PM10": body.get("pm10") or "",
            "主要污染物": body.get("primary_pollutant") or "无",
            "更新时间": body.get("ct") or "",
            "数据来源": "万维易源 ShowAPI",
        }, "获取成功", ttl=self.ttl)
