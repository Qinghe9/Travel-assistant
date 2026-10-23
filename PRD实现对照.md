# 旅游规划助手 · PRD v1.2 实现对照

> 检查日期：2026-10-23　｜　对照依据：`旅游规划助手_PRD_v1.2.docx` 与当前 `feature/agent` 代码
>
> 图例：✅ 已实现　🟡 部分实现　❌ 未实现

## 一、七类意图

| 意图 | 状态 | 当前实现 | 缺口 |
|---|---|---|---|
| INT-01 目的地信息 | ✅ | 高德 POI + RAG，名称/区域/地址/来源 | 无"开放状态"校验；无 Top5 相关性评测 |
| INT-02 行程规划 | 🟡 | 检索 + 天气 + POI + 路线 + 生成 | **输出是散文，不是结构化的逐日行程**；没有上午/下午/晚间、时长、交通衔接、预算小计、时间重叠校验 |
| INT-03 交通查询 | 🟡 | 高德驾车距离/耗时可用 | 航班、铁路需 Travelport 凭据，目前如实返回不可用 |
| INT-04 住宿查询 | 🟡 | RAG 可回答区域/住宿知识 | 实时酒店价格/库存不可用（无凭据） |
| INT-05 美食推荐 | ✅ | RAG 检索 + 引用 | 类别标注依赖 RAG 层，存在 tips 偏多问题 |
| INT-06 天气查询 | ✅ | Open-Meteo / 高德，支持单日、3 天、一周、两周 | 高德预报只有 4 天，超出会明确拒绝；无自动重试 |
| INT-07 闲聊兜底 | ✅ | 大模型或固定话术 | 无越界问题的统一拒答策略 |

> 路由层：**JevK5 结构化决策**已接入（`app/agent/jev.py`）。它只做 choice/noul/score
> 判断，返回 `confidence` 与全部选项概率；置信度 ≥0.55 时作为意图路由，否则回退主 LLM/规则。
> 实测网关延迟 25–35ms，不生成自由文本。

## 二、功能需求 FR-01 ~ FR-10

| 编号 | 要求 | 状态 | 说明 |
|---|---|---|---|
| FR-01 | 意图 + 结构化槽位；解析失败修复一次 | 🟡 | **JevK5 决策路由（confidence + probabilities）** + LLM 槽位 + Pydantic + 规则降级；**没有"修复一次"重试**；没有 intent/slot 评测集 |
| FR-02 | 目的地检索，展示名称/简介/位置/来源/时间 | 🟡 | 高德 POI 名称/区域/地址 + 来源；简介来自 RAG；**没有开放状态校验** |
| FR-03 | 逐日行程、时段、时长、交通、天气适配、预算小计、冲突检查 | ❌ | 目前只输出文字建议；`validate_plan` 只检查天数/预算粗略冲突 |
| FR-04 | 航班/铁路/酒店正式 API | 🟡 | Provider 抽象、缓存、熔断、超时齐备；**真实数据未接入**（无 Travelport 凭据） |
| FR-05 | 天气 API，失败重试 2 次指数退避 | 🟡 | 天气可用；缓存 30 分钟；**工具层没有重试/退避** |
| FR-06 | 向量 + BM25 + RRF，逐句引用，Top3≥95% | 🟡 | RAG 模块达标（基线 Recall@3 0.9845 / MRR 0.9601）；Agent 端有引用后置校验；**未做 Agent 级 RAG 评测**；绑定率常在 0.38~0.83（未绑定句子被剔除） |
| FR-07 | thread_id 记忆、10 轮、重启恢复 | 🟡 | SQLite 短期记忆、跨进程恢复已验证；长期偏好 Store 已加；**没有 10 轮回归**；历史只带最近 2 轮给模型；**工具摘要未持久化**；没有 summary/trimming |
| FR-08 | Chainlit 展示步骤与耗时 | 🟡 | 自研 `/demo` 已展示步骤/耗时/引用；**不是 Chainlit** |
| FR-09 | 点赞/点踩、SQLite、CSV 导出 | 🟡 | `/api/v1/feedback` 写 JSONL；**无 UI 按钮、未入 SQLite、无 CSV 导出** |
| FR-10 | 提示注入、密钥探测、越权参数 | 🟡 | 提示词含注入防护；工具参数按签名白名单过滤；**没有安全评测集、没有限流** |

## 三、非功能指标

| 指标 | 状态 | 证据 / 缺口 |
|---|---|---|
| 七类意图通过率 ≥95% | 🟡 | 手工冒烟覆盖，但无正式评测集 |
| 意图宏平均 ≥90% | ❌ | 无 `intent_eval.jsonl` |
| 槽位 F1 ≥90% | ❌ | 无标注集 |
| RAG Recall@3 ≥95% | ✅ | `evals/baseline.json`：0.9845（filter） |
| MRR ≥0.90 | ✅ | 0.9601 |
| 依据覆盖率 100% | 🟡 | 未绑定句子会被剔除，输出是"已绑定"；但绑定率指标口径需重做（当前 0.38~0.83） |
| 无关知识 0 条 | ✅ | RAG 评测 hard negative 为 0 |
| 首 token P95 ≤2s | 🟡 | RAG 模块有 `run_e2e_latency.py`；**Agent API 未记录 TTFT**；工具先执行 2~5s，实际首 token 可能超 2s |
| 完整响应/工具耗时 | 🟡 | 步骤里有单节点耗时；没有统一 P95 报告 |
| 连续 100 请求 ≥99% | ❌ | 无压测 |
| 冷启动 ≤60s | ✅ | 热索引约 2s，服务约 3s 可用 |
| 空闲内存 ≤1.5GB | ❌ | 未测 |
| 高危漏洞 0 | ❌ | 仅有 ruff；无 pip-audit / Trivy |
| 核心覆盖率 ≥80% | ❌ | 无 pytest-cov |

## 四、工程与交付物

| 项目 | 状态 | 缺口 |
|---|---|---|
| FastAPI + SSE | ✅ | `/api/v1/chat`、`/traces`、`/feedback`、`/profile`、`/health` |
| 双页面 UI | ✅ | `/` 导航页 + `/demo` DeepSeek 式对话台 |
| 短时记忆 | ✅ | SQLite checkpointer（同步 CLI / 异步 API） |
| 长期记忆 | ✅ | LangGraph Store，按 `user_id` 存偏好 |
| Dockerfile / Compose | ❌ | 无 `Dockerfile`、无 `compose.yaml`（PRD 要求 web/api/chroma 三服务） |
| Chainlit | ❌ | 未使用（PRD 指定） |
| tests/ 目录 | ❌ | 只有 `scripts/smoke_*` 冒烟 |
| intent/plan/safety 评测集 | ❌ | 只有 RAG 的 rag_eval / boundary_eval |
| GitHub Actions | 🟡 | 只有 `ci.yml`、`ai-review.yml`；缺 rag-eval、container、nightly |
| AI 辅助开发档案 | ❌ | 任务书硬性要求；`docs/` 被 gitignore |
| 系统设计/测试/部署文档、PPT | ❌ | 尚未产出 |
| 私有/公开来源指标 | ❌ | 现有 17553 chunk 全部 `source_type=public` |
| 数据文件完整性 | 🟡 | `data/scope_cities.json`、`data/china_scope_titles.json` 缺失（Agent 有内置城市表兜底） |
| 代码提交 | ❌ | Agent/API 代码仍全部未提交（`feature/agent` 工作区） |

## 五、最关键的 5 个风险

1. **FR-03 是最大评分缺口**：PRD 要的是"结构化逐日行程"，现在是散文。答辩时很难证明"时间不重叠、预算不超、每天结构完整"。
2. **2 秒 TTFT 目前保不住**：`classify → retrieve → tools → compose` 是串行的，工具 2~5s，首 token 自然超 2s。需要并行工具 + 更早出首字，或把"首 token"口径与评测方式说清楚。
3. **FR-04 无真实交通/住宿**：12306 无公开 API，Travelport 凭据难拿。必须把"接口预留 + 明确不可用"写进报告，避免被当成缺陷。
4. **`docs/` 被 gitignore**：任务书要求的开发档案、测试报告如果放 `docs/`，提交时会丢。建议改为 `reports/` 或 `ai-dev-log/`。
5. **Agent 层没有评测集**：目前所有"通过"都是冒烟，不是指标。老师问"意图准确率多少"时没有数字。

## 六、建议路线图

### P0（先保证课程验收）

1. **结构化行程**（FR-03）
   - 新增 `ItineraryPlan / ItineraryDay / Period` 模型；
   - `compose` 先产出 JSON，再渲染成文字；
   - `validate_plan` 检查：时间不重叠、预算不超、每天有上午/下午/晚间、交通衔接存在；
   - 支持"只改某一天"。
2. **Agent 评测集**
   - `intent_eval.jsonl`：7 类 × 30 条；
   - `plan_eval.jsonl`：≥50 条；
   - `safety_eval.jsonl`：≥30 条；
   - 输出宏平均、槽位 F1、约束满足率。
3. **TTFT 观测与优化**
   - 在 API 的 `meta/final` 里记录 `ttft_ms`；
   - 检索与工具并行（LangGraph Send）；
   - 工具慢时先流式输出"正在查询…"状态，再出正文。
4. **Docker Compose**
   - `web`（静态/UI）+ `api` + `chroma` 三服务；
   - 健康检查、持久卷、非 root、只读根文件系统。
5. **开发档案与报告**
   - `reports/`（不被 gitignore）：系统设计、测试报告、部署文档；
   - `ai-dev-log/`：提示词、AI 原始输出、bug 记录、人工修正；
   - 这份 PRD 对照表可直接作为"需求实现矩阵"。

### P1（质量与稳定性）

6. 工具重试/退避（FR-05），Provider 契约测试（429/5xx/坏 JSON）。
7. 反馈落 SQLite + UI 点赞/点踩 + CSV 导出（FR-09）。
8. 10 轮会话回归 + 历史裁剪/摘要；把工具摘要写入记忆（FR-07）。
9. 安全评测 + 限流 + 输入长度限制（FR-10）。
10. 真实 API 的失败注入测试（断网/超时/熔断恢复）。

### P2（完善与优化）

11. GitHub Actions 补齐：`rag-eval.yml`、`container.yml`、`nightly.yml`。
12. 私有知识条目（人工校订）与 private/public 指标。
13. 可观测性：trace_id 贯穿日志、节点耗时汇总、错误码。
14. 行程导出 Markdown / PDF；收藏与分享。
15. 多城市路线与"雨天 Plan B"。

## 七、可以补充的产品功能（不越 PRD 边界）

- **一键改某天**：基于结构化行程，只重排指定日期。
- **预算账本**：按天/类别汇总，超预算给出取舍建议。
- **天气备选方案**：下雨自动替换室内景点。
- **行程导出**：Markdown/PDF，方便答辩演示。
- **行程对比**：宽松/紧凑两版。
- **评测面板**：在 `/demo` 里展示最近一次意图/检索/绑定率。
- **多轮纠错**：用户说"第二天太赶"，只调整第 2 天。

> 注意 PRD 的非目标：不做支付下单、不做复杂用户画像、不做地图导航、不做移动端原生。
