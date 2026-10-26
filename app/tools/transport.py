# -*- coding: utf-8 -*-
"""交通与住宿工具：Travelport（航班 / 铁路 / 酒店）。

PRD 附录 B 已把 Travelport 正式账号列为待确认事项；FR-04 要求
「API 不可用时返回可解释错误，不生成替代数据」。所以这里只实现不可用路径，
把真实调用留给凭据到位之后 —— 在此之前绝不能出现任何价格或余票数字。
"""

from __future__ import annotations

import os

from app.tools.base import Provider, make_unavailable


class TravelportProvider(Provider):
    """航班/铁路/酒店适配器骨架，kind 取 flight / rail / hotel。"""

    name = "travelport"
    ttl = 0
    timeout = 5.0

    def __init__(self, kind: str = "flight"):
        super().__init__()
        self.kind = kind
        self.name = "travelport-%s" % kind

    @property
    def credentials(self) -> str:
        return os.environ.get("TRAVELPORT_CREDENTIALS", "").strip()

    def fetch(self, start_city: str = None, target_city: str = None,
              date: str = None, destination: str = None):
        label = "酒店" if self.kind == "hotel" else "航班/铁路"
        if not self.credentials:
            return make_unavailable(
                self.name,
                "未配置 Travelport 正式凭据，%s 查询暂不可用；该凭据在 PRD 附录 B 中"
                "仍属待确认事项，系统不会返回任何估计值" % label,
                "no_credentials")
        return make_unavailable(self.name, "凭据已配置，但真实调用尚未实现", "not_implemented")
