# 旅游规划助手 · RAG 模块

[![CI](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ci.yml)
[![代码自动审查](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ai-review.yml/badge.svg)](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ai-review.yml)

面向《人工智能创新开发实践》课程设计的旅游规划助手 RAG 部分：把维基导游、GeoNames、ChinaTravel 等公开数据建成**可追溯**的知识库，提供「采集 → 清洗 → 切分 → 混合检索 → 证据绑定 → 生成 → 引用校验」的端到端链路，并对 Recall@3、MRR、无关知识引入、证据绑定率、检索时延做自动评测。

> 本仓库**只收录代码与评测集**（`app/`、`scripts/`、`evals/`），外加 CI 配置与说明文件。数据集、索引、PRD/流程文档与插图只保留在本机，不入库（见下方「数据与文档不入库」）。

## 目录结构

```
app/rag/
  config.py            配置加载（.env + 默认值）
  indexing/            采集与建库：sources/、clean、split、geo、align、embed、build
  retrieval/           混合检索：recall（向量 + BM25）、fuse（RRF）、evidence（证据口径）
  generation/          llm 客户端、answer（提示词 + 引用后置校验）
scripts/               数据下载、清洗、切分、建索引、问答、自检等可执行入口
evals/                 检索评测脚本 + 评测集（Recall@3 / MRR / nDCG / 时延门禁）
.github/               CI 与代码自动审查工作流
```

## 快速开始

```bash
# 1) 依赖
python -m pip install -r requirements-rag.txt

# 2) 配置（复制模板后填密钥；.env 不会被提交）
cp .env.example .env

# 3) 拉取数据集（默认跳过可选项；--check 只打印计划）
python scripts/fetch_datasets.py --check
python scripts/fetch_datasets.py

# 4) 数据集就绪度自检
python scripts/check_dataset_ready.py

# 5) 建索引（先小样本试跑，再全量重建）
python scripts/build_index.py --limit 200
python scripts/build_index.py --rebuild

# 6) 端到端问答
python scripts/ask.py "成都有哪些必去的景点" --city 成都 --show-evidence
```

检索评测脚本与评测集（`evals/run_rag_eval.py`、`evals/rag_eval.jsonl`）都在仓库里，直接 `python evals/run_rag_eval.py --mode both` 即可运行。

## 检索架构：三路召回 + 重排 + 多轮

| 环节 | 做法 | 实测 |
|---|---|---|
| 第 1 路 向量 | 内存暴力余弦（12k 已归一化向量） | ~5 ms |
| 第 2 路 BM25 | 倒排候选 + numpy 向量化打分（与 rank_bm25 逐位一致） | ~4 ms |
| 第 3 路 关键词/标题 | 标题倒排加权 + 关键词覆盖率，专治专名/景点名 | ~10 ms |
| 融合 | RRF（按名次，避免分数量纲不可比），三路等权 | <1 ms |
| 重排 | 硅基流动 `BAAI/bge-reranker-v2-m3`，失败自动退回融合序 | ~0.35 s |
| 生成 | 硅基流动免费档 `inclusionAI/Ling-mini-2.0`（实测 p50 1.58 s、引用 100% 合规） | ~1.2 s |
| 多轮 | 查询改写（省略追问补全）+ 槽位继承（城市/类别跨轮不丢） | ~0.5 s/追问 |

- **重排的价值**：open 模式（不传过滤）Recall@3 0.8903→0.9226、MRR 0.7871→0.8398；把重排候选调到 20 效果最好（默认 8 是时延与质量折中）。
- **类别过滤不可靠**：chunk 的 `category` 里 tips 占 53.9%（成都 66 个 chunk 里 food=0），硬过滤会把池子清空，
  因此在 [fuse.py](app/rag/retrieval/fuse.py) 里做了「滤空就退回只按城市」的自动降级。
- **端到端时延**：`python evals/run_e2e_latency.py`，p50 ≈1.7 s、均值 ≈2.0 s、P95 ≈2.9 s；
  瓶颈是免费档 LLM 的延迟尖刺（单次偶发 3~8 s），检索侧只占 ~0.37 s。
  生成超时会退化为**抽取式作答**（原文句 + 引用编号），保证"有据可查"而非报错。

## 检索性能（进程内热索引）

[app/rag/retrieval/hot_index.py](app/rag/retrieval/hot_index.py) 把向量矩阵、正文元数据、词法倒排一次性装进内存，检索阶段从「Chroma 往返 + 全库 BM25 打分」变成「内存矩阵乘法 + 倒排候选」：

| 阶段 | 热索引前 | 热索引后 |
|---|---|---|
| 向量召回 | 126 ms | ~11 ms |
| BM25 召回 | 129 ms | ~5 ms |
| 元数据回表 | 117 ms | ~0.2 ms |
| **单次检索** | **476 ms（P95 686）** | **19 ms（P95 21）** |

- 由 `recall.warmup()` 构建（长驻服务启动时调用一次），构建失败自动回退 Chroma 路径；
- 向量首次从 Chroma 拉全量后缓存到 `data/index/hot_vectors.npz`，随 `index_version` 失效重取；
- 评测门禁已补上「检索时延 P95 ≤ 300ms」——此前只是文档里的指标，没有门禁守着。

## 数据与文档不入库

[.gitignore](.gitignore) 排除了 `data/`（原始快照、清洗产物、向量库与 manifest，约 1 GB）、`docs/`、`prd_assets/`、`rag_assets/`、`prd_render*/`、`*.docx` 等，本机按上面的命令重建。

> 采集脚本已内置 HTTP 308 跳转处理：`hf-mirror.com` 会把 `/datasets/.../resolve/...` 用 308 跳到 `huggingface.co`，而 Python 3.10 及更早的标准库既不派发 `http_error_308`，`redirect_request()` 也不在白名单里，必须显式补上（见 `scripts/fetch_datasets.py` 的 `_RedirectHandler`）。脚本同时校验 `Content-Length`，避免把**截断**的快照当成成功登记。

## 持续集成与代码自动审查

| 工作流 | 触发 | 内容 |
|---|---|---|
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | push / PR | ruff 高信号规则（E9/F63/F7/F82）+ 全量语法编译 + **离线冒烟**（不联网、不需要密钥） |
| [.github/workflows/ai-review.yml](.github/workflows/ai-review.yml) | PR | ① ruff 把问题直接标注到 PR 改动行；② 调用 DeepSeek 生成评审意见并回帖 PR |

启用 AI 审查只需在 **Settings → Secrets and variables → Actions** 添加：

- Secret `DEEPSEEK_API_KEY`：AI 审查用的密钥（**不配置也能跑**，脚本会检测到缺失并跳过，不会让 CI 变红）
- 可选 Variables：`REVIEW_API_BASE`（默认 `https://api.deepseek.com/v1`）、`REVIEW_MODEL`（默认 `deepseek-chat`）

> fork 发起的 PR 拿不到仓库 Secret，AI 审查会自动跳过。

## 贡献与分支约定

- `main` 只接受通过 PR 合入。
- 每个 PR 自动跑两道门：**CI**（ruff 高信号规则 + 全量语法编译 + 离线冒烟）与**代码自动审查**（ruff 把问题标注到改动行；配置 `DEEPSEEK_API_KEY` 后模型还会给出评审意见并回帖）。
- 提交信息建议写成 `<type>(<scope>): <subject>`，type 取 feat / fix / docs / chore / refactor。

## 许可

数据来源与许可见 [NOTICE](NOTICE)；其中 CC BY-SA / CC BY-NC 等条款在公开或商用场景下需按 NOTICE 声明。
