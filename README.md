# 旅游规划助手 · RAG 模块

[![CI](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ci.yml)
[![代码自动审查](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ai-review.yml/badge.svg)](https://github.com/Qinghe9/Travel-assistant/actions/workflows/ai-review.yml)

面向《人工智能创新开发实践》课程设计的旅游规划助手 RAG 部分：把维基导游、GeoNames、ChinaTravel 等公开数据建成**可追溯**的知识库，提供「采集 → 清洗 → 切分 → 混合检索 → 证据绑定 → 生成 → 引用校验」的端到端链路，并对 Recall@3、MRR、无关知识引入、证据绑定率、检索时延做自动评测。

| 文档 | 说明 |
|---|---|
| [RAG模块开发流程_v1.1.md](RAG模块开发流程_v1.1.md) | 阶段 A–H 的详细开发流程与指标口径 |
| [docs/数据源调研_v1.md](docs/数据源调研_v1.md) | 各数据源可达性实测、许可与字段说明 |
| [RAG模块开发流程_v1.1.docx](RAG模块开发流程_v1.1.docx) | 流程文档的可交付版本 |
| [NOTICE](NOTICE) | 数据来源与许可声明 |

## 目录结构

```
app/rag/
  config.py            配置加载（.env + 默认值）
  indexing/            采集与建库：sources/、clean、split、geo、align、embed、build
  retrieval/           混合检索：recall（向量 + BM25）、fuse（RRF）、evidence（证据口径）
  generation/          llm 客户端、answer（提示词 + 引用后置校验）
scripts/               数据下载、清洗、切分、建索引、问答、自检等可执行入口
evals/                 rag_eval.jsonl 评测集与 run_rag_eval.py 指标脚本
docs/                  数据源调研与报告
data/                  数据集与索引（**不入库**，见下）
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

# 7) 检索评测（Recall@3 / MRR / nDCG@3 / 时延）
python evals/run_rag_eval.py --mode both
```

## 数据集不入库

`data/` 下的原始快照、清洗产物与向量库合计约 1 GB，全部由 [.gitignore](.gitignore) 排除，本机按上面的命令重建即可；仓库只保留 `data/manifest.csv` 这份**来源与许可登记表**（doc_id / source_url / license / content_hash / bytes）。

> 采集脚本已内置 HTTP 308 跳转处理：`hf-mirror.com` 会把 `/datasets/.../resolve/...` 用 308 跳到 `huggingface.co`，而 Python 3.10 及更早的标准库既不派发 `http_error_308`，`redirect_request()` 也不在白名单里，必须显式补上（见 `scripts/fetch_datasets.py` 的 `_RedirectHandler`）。脚本同时校验 `Content-Length`，避免把**截断**的快照当成成功登记。

## 持续集成与代码自动审查

| 工作流 | 触发 | 内容 |
|---|---|---|
| [.github/workflows/ci.yml](.github/workflows/ci.yml) | push / PR | ruff 高信号规则（E9/F63/F7/F82）+ 全量语法编译 + **离线冒烟**（不联网、不需要密钥） |
| [.github/workflows/ai-review.yml](.github/workflows/ai-review.yml) | PR | ① ruff 把问题直接标注到 PR 改动行；② 调用 DeepSeek 生成评审意见并贴回 PR |

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
