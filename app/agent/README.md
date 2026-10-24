# Agent 层设计说明

对应 PRD v1.2 第 3、4、7 章：单个 LangGraph 状态图，把"理解 → 追问 → 调工具/API →
补充 RAG → 校验 → 作答 → 落盘"串成一条可追溯的流水线。RAG 复用现有 `app/rag`，不重复实现。

## 目录

```
app/models/schemas.py     数据模型：Intent / SlotBundle / IntentResult / ToolResult / SourceChunk / AgentReply
app/agent/
  config.py               配置（复用 app.rag.config 的 .env，只补 Agent 自己的开关）
  intents.py              七类意图 + 必填槽位 + 工具/检索编排表   ← 改需求只改这里
  state.py                LangGraph 共享状态 AgentState
  slots.py                规则式槽位抽取（降级路径）+ 文案归一化
  prompts.py              提示词集中管理（含注入防护措辞）
  llm.py                  大模型调用（复用 app.rag.generation.llm）+ JSON 校验
  retrieval.py            检索适配层：把 RAG 包装成"可降级"
  nodes.py                主链、缺槽、闲聊、解释追问与酒店 RAG 回退节点
  graph.py                主图组装与编译
  memory.py               checkpointer（sqlite / memory）
app/tools/
  base.py                 Provider 抽象：ToolResult + TTL 缓存 + 熔断 + 计时
  weather.py              Open-Meteo 天气（无需 Key，直接可用）
  amap.py                 高德：景点/酒店/餐饮 POI、驾车/公交换乘、天气
  showapi.py              万维易源：节假日/调休、城市空气质量（免费接口）
  poi.py                  OpenTripMap 景点（需 Key）
  route.py                路线距离骨架（需 Key）
  transport.py            航班/铁路/酒店骨架（需 Travelport 凭据）
  registry.py             意图 -> 工具 -> 证据/引用
scripts/run_agent.py           命令行入口（单轮 / 多轮）
scripts/smoke_agent_offline.py 离线自检（不联网、不需要密钥）
app/api/                 FastAPI 路由 + SSE（/api/v1/chat、/traces、/feedback、/health）
app/agent/streaming.py   LangGraph 执行过程 → SSE 事件（meta/step/token/final/error）
```

## 七类功能怎么走

| 意图 | 例子 | 必填槽位 | 路径 | 用到的能力 |
|---|---|---|---|---|
| INT-01 目的地信息查询 | 成都有什么好玩的 | destination | tools → retrieve | 高德景点优先 + 知识库背景 |
| INT-02 行程规划 | 规划 3 天西安行程 | destination, days | tools → retrieve | 实时工具优先 + 知识库背景 |
| INT-03 交通查询 | 广州到长沙高铁 / 天河区到广州塔怎么坐地铁 | origin, destination_place（高铁加 date） | tools → retrieve | 高德驾车/公交优先 + 知识库补充 |

> 高德没有 12306 车次/余票接口：公交/地铁可查线路与站点，高铁车次必须由正式铁路供应商提供，缺失时如实说明。
| INT-04 住宿查询 | 广州天河区附近有什么酒店 | destination | tools → retrieve | 高德酒店 POI 优先 + 知识库区域背景 |
| INT-05 美食推荐 | 杭州有什么好吃的 | destination | tools → retrieve | 高德餐厅名称优先 + RAG 菜品/小吃 |
| INT-06 天气查询 | 明天长沙天气怎样 | destination, date | tools → retrieve | 天气工具优先；RAG 只做背景，不替代实况 |
| INT-07 闲聊与兜底 | 你好；谢谢 | 无 | chat | 大模型（无则固定话术） |

两条边界（PRD 第 6.4 节）已在编排表里落实：

1. 所有旅行意图统一先执行工具/API，再执行 RAG 补充；
2. 提示词中的证据顺序和预算固定为“工具在前、RAG 在后”，冲突时实时工具优先；
3. 天气/票价/余票只能来自工具；RAG 可作背景，但不能伪装成实时值。

## 八个节点（PRD 第 4.1 节）

| 节点 | 做什么 | 失败策略 |
|---|---|---|
| `normalize_input` | 取本轮问题、归一化，并清空上一轮临时字段 | 空输入直接提示 |
| `classify_and_extract` | 意图 + 槽位；高置信 Jev + 完整规则槽位时跳过主 LLM | 失败降级为规则式，不阻断 |
| `route` | 第一跳：ask / chat / tools / explain | 未知意图进兜底 |
| `call_tools` | 同一意图下工具并行；高德共享地理编码缓存与 QPS 限速 | 超时/熔断/无凭据 → unavailable |
| `retrieve` | 工具执行完成后补充 RAG，只追加证据，不覆盖 API 结果 | RAG 不可用则保留已有工具证据 |
| `validate_plan` | 冲突检查（缺天数、预算过低、天气非工具来源） | 只报可核实的冲突 |
| `compose` | 逐句绑定 `[source_id]` 作答 | 生成失败 → 抽取式兜底 |
| `persist` | 轨迹落盘（JSONL） | 落盘失败不影响本轮答案 |

分支节点：`ask_clarify`（缺槽追问）、`chat`（闲聊兜底）、`explain`（解释上一轮推荐依据）。

解释追问不会重新跑 Jev、LLM、RAG 或工具。`persist` 会把上一轮问题的意图、槽位、引用和工具结果
存进 `last_turn`；用户随后问“为什么选这些酒店/依据是什么”时，直接进入 `explain` 节点解释筛选
范围、排序方式与数据局限，连续追问也不会覆盖最初的推荐对象。

## 状态字段

`thread_id` / `messages`（追加）/ `query` / `intent` / `confidence` / `slots` / `missing`
/ `evidence` / `citations` / `evidence_meta` / `tool_results` / `plan` / `answer`
/ `degraded` / `no_evidence` / `evidence_binding_rate` / `follow_up` / `last_turn` / `trace`（追加）

`messages` 与 `trace` 用 `operator.add` 追加；其余字段每轮覆盖
（`normalize_input` 会主动清空上一轮的证据类字段，避免多轮用到过期证据）。

## 证据绑定

知识证据与工具证据统一成同一形状，都带 12 位十六进制 `source_id`：

- 知识证据：`sha1(source_url + '#' + chunk_id)[:12]`（沿用 RAG 层口径）；
- 工具证据：`sha1("tool:" + provider + ":" + fetched_at)[:12]`。

统一之后，`compose` 只需要一套逐句校验（复用 `app/rag/generation/answer.py` 的
`post_validate`）。未绑定引用的句子会被整句剔除，`evidence_binding_rate` 单独上报。

## 怎么跑

```bash
# 依赖（RAG 层依赖也要装）
python -m pip install -r requirements-rag.txt -r requirements-agent.txt

# 单轮
python scripts/run_agent.py "帮我规划3天西安行程"
python scripts/run_agent.py "明天北京天气" --show-trace

# 多轮
python scripts/run_agent.py --chat

# 离线自检（不联网、不需要密钥）
python scripts/smoke_agent_offline.py
```

没有密钥也能跑：意图走规则识别、答案走抽取式兜底。这不是 mock ——
两条降级路径都不会编造事实，只是不调用大模型。

## 新增一类意图 / 一个工具

- 加意图：改 `intents.py` 的 `REQUIRED_SLOTS` / `INTENT_TOOLS` / `INTENT_NEEDS_RETRIEVAL`，
  再在 `slots.py` 的 `_INTENT_RULES` 加触发词；节点代码不用动。
- 加工具：继承 `app/tools/base.py` 的 `Provider`，实现 `fetch()`，在 `registry.get_provider`
  注册，最后挂到 `INTENT_TOOLS`。

## 环境变量（都有默认值，不配也能跑）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_MEMORY` | `sqlite` | `sqlite` 跨进程保留；`memory` 仅进程内 |
| `AGENT_DB_PATH` | `data/agent/agent.db` | SQLite 会话库 |
| `AGENT_PROFILE_DB` | `data/agent/profile.db` | 长期偏好库（按 user_id） |
| `AGENT_DATE_TTL_HOURS` | `24` | 日期槽位多久未更新就清空 |
| `AGENT_TRACE_LOG` | `data/agent/traces.jsonl` | 轨迹落盘位置 |
| `AGENT_TRACE_ENABLED` | `1` | 关掉则不写轨迹 |
| `AGENT_CLASSIFY_MAX_TOKENS` | `256` | 意图识别输出上限 |
| `AGENT_ALLOW_RULE_FALLBACK` | `1` | 允许无密钥时的规则式降级 |
| `OPENTRIPMAP_API_KEY` | 空 | 景点工具 |
| `AMAP_API_KEY` | 空 | 高德 Web 服务：景点 POI、路线距离、可选天气 |
| `AMAP_MIN_INTERVAL` | `0.36` | 高德请求最小间隔（秒），适配个人 Key 的 QPS 限制 |
| `WEATHER_PROVIDER` | `open-meteo` | 天气数据源：`open-meteo` 或 `amap` |
| `ORS_API_KEY` | 空 | 路线距离工具 |
| `TRAVELPORT_CREDENTIALS` | 空 | 航班/铁路/酒店（PRD 附录 B 待确认事项） |
| `JEV_APP_KEY` | 空 | JevK5 决策路由（ShowAPI appKey） |
| `JEV_MODE` | `gateway` | `gateway`（route.showapi.com）或 `native`（jev.showapi.com） |
| `JEV_MIN_CONFIDENCE` | `0.55` | 低于该值不采纳 Jev 路由，回退主 LLM |
| `JEV_TRUST_CONFIDENCE` | `0.75` | 与主 LLM 冲突时，达到该值才覆盖 |
| `SHOWAPI_APP_KEY` | 空（回退 JEV_APP_KEY） | 万维易源节假日/空气质量接口 |

## 已知边界

1. 住宿、景点、餐饮、驾车和公交换乘已接高德；Travelport 航班/铁路正式凭据仍是 PRD 附录 B
   的待确认事项，没有凭据时不会编造票价、车次或余票。
2. 高德公交接口可能返回跨城铁路组合，但不等于 12306 实时余票；答案中必须明确这一边界。
3. 住宿工具只返回名称/区域/地址，没有价格、评分和房型，因此“为什么选这些酒店”只能解释
   检索范围与顺序，不能冒充性价比排名。
4. 任务书的 2 秒指首 token 时间（TTFT P95），不是整段答案的总耗时。本层已使用 SSE 流式输出，
   并对 Jev 快速路由、工具并行、地理编码缓存和高德限速做了优化；仍需在真实网络环境下跑 20 次
   并记录 P95，不能用单次总耗时代替 TTFT 指标。
