# T31–T33 全国行政地名覆盖开发记录

## 提示词原文

> 用户输入任意中国行政地名（省/市/县/县级市，如“安顺”“凯里”“婺源”“敦煌”
> “平潭”）时，天气必须查得到（在 API 覆盖范围内），高德 POI/酒店/餐饮检索
> 中心必须正确落在该地名，不允许退化成泛化搜索。

## AI 原始输出与问题

- 小城市依赖 `BASE_CITIES`，县级地名容易抽不出；
- 天气工具单独调用 Open-Meteo geocoding，高德工具各自解析区域，来源与缓存不统一；
- 省级 default 只读取硬编码省会表；
- 县级 `scope` 未纳入规则城市识别，高德检索中心可能退化为泛化词。

## 根因

1. 地名解析没有统一入口；
2. 高德结果只有进程内缓存，没有跨进程 SQLite 缓存；
3. `build_scope.py` 没有 ADM3 县级输出；
4. `call_tools` 只透传槽位原文，缺少解析来源 trace。

## 修改

- 新增 `app/agent/geocode.py`，固定使用 scope → 高德 geocode → GeoNames 三级链；
- 高德结果写入 `data/agent/geocode_cache.sqlite`，所有请求继续走全局限速器；
- `OpenMeteoWeather` 与 `AmapWeather` 共用坐标/adcode 解析；
- `call_tools` 将县级解析结果作为高德检索中心，并把 `source/attempts/adcode` 写入 trace；
- `build_scope.py --level county` 生成 `data/scope_counties.json`；
- `slots.cities()` 同步加载县级表；
- T29 省会优先取统一解析结果，硬编码 `PROVINCES` 仅作离线兜底；
- 新增 geocode、天气覆盖、POI 透传、县级槽位测试。

## 复测

```powershell
python -m pytest tests/test_geocode.py tests/test_weather_coverage.py tests/test_weather_days.py tests/test_poi_passthrough.py tests/test_county_scope.py -q
```

结果：31 项全量测试通过；Agent 离线冒烟 135 项、API 离线冒烟 31 项全部通过。

数据资产校验：

- GeoNames `CN.zip`：32,235,307 字节，ZIP 结构与 CRC 校验通过；
- `scope_cities.json`：360 个地级市；
- `scope_counties.json`：2870 个县级地名；
- `geo_index.json`：6631 个名称记录、33 组人工别名。

真实五条问句复测：

| 输入 | 结果 |
|---|---|
| 安顺2天行程 | INT-02；destination=安顺；天气 OK；POI 中心=安顺；source=scope |
| 凯里明天天气 | INT-06；destination=凯里；天气 OK；source=scope |
| 婺源有什么好玩的 | INT-01；destination=婺源；POI 中心=婺源；source=scope |
| 敦煌3天行程，天气怎么样 | INT-02+INT-06；destination=敦煌；3 日天气 OK；POI 中心=敦煌；source=scope |
| 贵州3天行程 | INT-02；destination=贵阳；source=province_default；POI 中心=贵阳 |

复测中所有工具的检索中心都落在用户给出的市/县，没有再退化为泛化搜索。
