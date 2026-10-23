# -*- coding: utf-8 -*-
"""路线距离工具：openrouteservice。

PRD 第 7 章给的降级是「直线距离 + 明确标注估算」。本骨架先只实现
「未配置密钥就如实说不可用」，不预置任何坐标表 —— 坐标属于静态数据，
一旦过期就会变成事实错误，宁可不说。
"""

from __future__ import annotations

import os

from app.tools.base import Provider, make_unavailable


class OpenRouteService(Provider):
    """路线距离适配器骨架。"""

    name = "openrouteservice"
    ttl = 86400
    timeout = 4.0

    @property
    def api_key(self) -> str:
        return os.environ.get("ORS_API_KEY", "").strip()

    def fetch(self, start_city: str = None, target_city: str = None):
        if not self.api_key:
            return make_unavailable(self.name, "未配置 ORS_API_KEY，路线距离暂不可用", "no_api_key")
        return make_unavailable(self.name, "适配器骨架已就绪，尚未接入坐标解析", "not_implemented")
