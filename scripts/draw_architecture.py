#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绘制 RAG 模块系统架构图（按当前真实实现）。输出 docs/system_architecture.png"""
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parents[1]
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

FIG_W, FIG_H = 15.2, 18.6
fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, 100); ax.set_ylim(-10, 100); ax.axis("off")
UNIT = (FIG_W * 72) / 100.0


def tw(s, f):
    w = sum(1.0 if ord(c) > 0x2E80 else 0.55 for c in s)
    return w * f / UNIT


def wrap(s, maxw, f):
    lines = []
    for seg in s.split("\n"):
        cur = ""
        for ch in seg:
            if tw(cur + ch, f) <= maxw:
                cur += ch
            else:
                lines.append(cur); cur = ch
        lines.append(cur)
    return "\n".join(lines)


def box(x, y, w, h, title, sub="", fc="#EAF2F8", ec="#2F5597", tf=8.6, sf=7.0):
    ax.add_patch(FancyBboxPatch((x + 0.3, y - 0.3), w, h, boxstyle="round,pad=0.15,rounding_size=0.5",
                                fc="#00000010", ec="none", zorder=2))
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.15,rounding_size=0.5",
                                fc=fc, ec=ec, lw=1.1, zorder=3))
    cx, pad = x + w / 2, 0.9
    t = wrap(title, w - 2 * pad, tf)
    n_t = t.count("\n") + 1
    s = wrap(sub, w - 2 * pad, sf) if sub else ""
    n_s = (s.count("\n") + 1) if s else 0
    th = n_t * tf / UNIT * 1.5
    sh = n_s * sf / UNIT * 1.5
    total = th + sh + (0.6 if sh else 0)
    top = y + h / 2 + total / 2
    ax.text(cx, top - th / 2, t, ha="center", va="center", fontsize=tf, fontweight="bold",
            color="#17202A", zorder=4, linespacing=1.45)
    if s:
        ax.text(cx, top - th - 0.6 - sh / 2, s, ha="center", va="center", fontsize=sf,
                color="#34495E", zorder=4, linespacing=1.5)


def band(y1, y2, badge, title, color="#1F4E78"):
    ax.add_patch(FancyBboxPatch((1.4, y1), 69.4, y2 - y1, boxstyle="round,pad=0.25,rounding_size=0.7",
                                fc="#F5F9FD", ec="#B9D2EA", lw=1.2, zorder=1))
    ax.text(3.7, y2 - 1.55, badge, ha="center", va="center", fontsize=9.5, fontweight="bold",
            color="white", zorder=4, bbox=dict(boxstyle="round,pad=0.3", fc=color, ec="none"))
    ax.text(5.9, y2 - 1.55, title, ha="left", va="center", fontsize=10.5,
            fontweight="bold", color=color, zorder=4)


def row(y, h, items, x1=2.8, x2=69.4, gap=1.2, tf=8.6, sf=7.0):
    n = len(items)
    w = (x2 - x1 - gap * (n - 1)) / n
    for i, it in enumerate(items):
        fc, ec = (it[2], it[3]) if len(it) > 2 else ("#EAF2F8", "#2F5597")
        box(x1 + i * (w + gap), y, w, h, it[0], it[1] if len(it) > 1 else "",
            fc=fc, ec=ec, tf=tf, sf=sf)


def arrow(x, y1, y2, label="", color="#2F5597"):
    ax.add_patch(FancyArrowPatch((x, y1), (x, y2), arrowstyle="-|>", mutation_scale=15,
                                 lw=1.8, color=color, zorder=5))
    if label:
        ax.text(x + 1.0, (y1 + y2) / 2, label, fontsize=8.2, color=color, va="center", zorder=6)


OK, HL, OUT = "#EAF2F8", "#D4E6F9", "#E8F8F0"
HL_E, OUT_E = "#1F6FB2", "#1E8449"
RED, RED_E = "#FDEDEC", "#C0392B"

ax.text(50, 99.0, "旅游规划助手 · RAG 模块系统架构", ha="center", fontsize=18,
        fontweight="bold", color="#17202A")
ax.text(50, 97.1, "核心链路已实现并验收通过 · 虚线框为尚未实现的 Agent 层与 CI 门禁",
        ha="center", fontsize=9.2, color="#6B7B8C")

band(88.1, 96.2, "1", "数据源层（4 类，全部登记许可）")
row(88.6, 5.2, [("中文维基导游 dump", "8,412 篇 · 13.7 MB\nCC BY-SA 4.0"),
                ("中文维基百科 dump 分片", "multistream + Range\n按需取 1,999 篇 · CC BY-SA"),
                ("GeoNames + 魔搭 POI", "地名 96 万行 · 全国 POI\nCC BY 4.0 / ODbL"),
                ("人工校订语料", "未做 · private 层\n任务书要求", RED, RED_E)])

arrow(36, 87.9, 86.9, "采集 / 许可登记 / sha256")
band(80.7, 86.8, "2", "原始快照与许可登记（出口标准已满足）")
row(80.9, 3.2, [("data/raw/<source>/<日期>/", "只追加不覆盖 · 33 个文件"),
                ("manifest.csv（2,032 行）", "URL / 许可 / 时间 / sha256"),
                ("NOTICE（13 个数据源）", "CC BY-SA / CC BY / ODbL 署名")], tf=8.2, sf=6.8)

arrow(36, 80.5, 79.5, "清洗 + 地名对齐")
band(65.7, 79.4, "3", "清洗与地名对齐（已完成）")
row(71.2, 5.0, [("去模板与噪声", "剥离 100+ 类模板\n删导航 / 页脚 / 外链"),
                ("POI 卡片还原", "39,571 条 listing\n→ 名字（地址；电话；时间）"),
                ("文本规范化", "繁→简（zhconv）\n全角半角 / 零宽字符"),
                ("去重 + 地名对齐", "shingle 0.9 / 哈希\naliases 65,782 条", HL, HL_E)])
row(66.4, 4.0, [("documents.jsonl（2,777 篇 · 707.8 万字）", "含 province / city / county 归属", OUT, OUT_E),
                ("dead_links.csv · geo_index.json · aliases.json", "维基百科补充 1,999 篇", OUT, OUT_E)],
    tf=8.2, sf=6.8)

arrow(36, 65.5, 64.5, "语义切分")
band(51.0, 64.4, "4", "语义切分（已完成）")
row(56.5, 5.0, [("按 H2 / H3 分节", "标题栈 → title_path\n先分节再控长度"),
                ("350–600 字成块", "重叠 80 字对齐句子\n超长句按逗号硬切"),
                ("跨小节归并", "<200 字向前 / 向后合并\n中位 455 字"),
                ("类别打标", "listing 类型 →\nattraction / food / hotel", HL, HL_E)])
row(51.7, 4.0, [("chunks.jsonl —— 12,022 个 chunk / 453.3 万字", "", OUT, OUT_E),
                ("覆盖 33 个省级行政区 · 360 个地级市（无空白）", "", OUT, OUT_E)], tf=8.4, sf=6.8)

arrow(36, 50.8, 49.8, "向量化 + 建索引")
band(35.9, 49.7, "5", "向量化与索引（验收 10/10 PASS）")
row(41.4, 5.4, [("向量化 BAAI/bge-m3", "1024 维 · API 调用\n批 32 / 重试 / 熔断 / 兜底"),
                ("Chroma travel_kb_v1", "12,022 × 1024 · cosine\n元数据 15 字段全量写入"),
                ("BM25 索引 bm25.pkl", "jieba 分词 · k1=1.5 b=0.75\n与向量双路召回")], tf=8.6, sf=6.9)
row(36.6, 4.0, [("embed_cache.sqlite（按 content_hash 永久缓存）", "", OUT, OUT_E),
                ("IndexManifest.json（版本 / 块数 / 维度 / BM25 参数）", "", OUT, OUT_E)], tf=8.2, sf=6.8)

arrow(36, 35.7, 34.7, "混合检索")
band(22.3, 34.6, "6", "混合检索与融合（已完成）")
row(27.7, 4.0, [("用户问题", "含 city / category 过滤"),
                ("同一 bge-m3 向量化", "查询与库必须同模型"),
                ("向量 Top10", "Chroma cosine"),
                ("BM25 Top10", "jieba 词法兜底")], tf=8.4, sf=6.8)
row(23.0, 4.0, [("RRF 融合 k=60 → 同 parent 最多 2 条 → Top3", "同分优先向量召回", HL, HL_E),
                ("双阈值：余弦 ≥ 0.50 / RRF ≥ 0.018", "不过阈值 → 无证据分支", HL, HL_E)], tf=8.2, sf=6.8)

arrow(36, 22.1, 21.1, "证据装配 + 生成")
band(12.8, 21.0, "7", "证据装配、生成与引用绑定（已完成）")
row(13.5, 4.6, [("RagEvidence", "source_id = sha1(url#id)[:12]\n引用一一对应"),
                ("DeepSeek deepseek-chat", "15s 超时 / 1 重试\n只依据证据 / 防注入"),
                ("逐句引用后置校验", "无合法 source_id\n的句子直接剔除", HL, HL_E),
                ("带引用答案 / 无证据拒答", "绑定率实测 1.0000", OUT, OUT_E)], tf=8.2, sf=6.8)

arrow(36, 12.6, 11.7, "评测与门禁")
band(4.5, 11.5, "8", "评测（已完成）")
row(5.2, 3.4, [("rag_eval 155 条 + boundary_eval 40 条", "省会 / 地级市 / 县级 / 跨城 + 难负例", OUT, OUT_E),
               ("Recall@3 0.9742 · MRR 0.9667 · 无关知识 0 · 绑定率 1.0", "baseline.json 已锁定", OUT, OUT_E)],
    tf=8.2, sf=6.8)

ax.add_patch(FancyBboxPatch((72.0, 12.8), 26.4, 83.4, boxstyle="round,pad=0.25,rounding_size=0.7",
                            fc="#FEFBF0", ec="#D4AC0D", lw=1.3, zorder=1))
ax.text(85.2, 94.2, "横切能力与运维", ha="center", fontsize=11.5, fontweight="bold",
        color="#7D6608", zorder=3)
cross = [("配置中心", "config.py 读 .env\nRAG_* / DEEPSEEK_* 可覆盖"),
         ("向量缓存", "embed_cache.sqlite\n按 content_hash 永久复用"),
         ("熔断降级", "60s 内 5 次失败断开\n兜底模型不一致时报错"),
         ("服务预热", "warmup() 预热 jieba / BM25\n/ Chroma（冷启动 3~15s）"),
         ("四段耗时上报", "embed / vector / bm25 / fuse\n外加 meta"),
         ("双阈值设计", "余弦 0.50（绝对语义）\nRRF 0.018（挡单路孤证）"),
         ("索引可复现", "IndexManifest + 快照日期\n+ git_commit"),
         ("评测同源校验", "评测集 source_id 与索引\n不一致直接报错"),
         ("两条接口约定", "① 必须传 city\n② 问景点 / 美食必须带 category")]
yy = 91.0
for t, s in cross:
    box(73.2, yy - 6.0, 24.0, 6.0, t, s, fc="#FFFDF5", ec="#D4AC0D", tf=8.6, sf=6.8)
    yy -= 8.4

ax.add_patch(FancyBboxPatch((1.4, -8.6), 97.0, 8.6, boxstyle="round,pad=0.25,rounding_size=0.7",
                            fc="#F8F1FC", ec="#7D3C98", lw=1.4, ls="--", zorder=1))
ax.text(3.7, -2.2, "A", ha="center", va="center", fontsize=9.5, fontweight="bold", color="white",
        zorder=4, bbox=dict(boxstyle="round,pad=0.3", fc="#7D3C98", ec="none"))
ax.text(5.9, -2.2, "Agent 层与 CI 门禁（尚未实现）", fontsize=10.5, fontweight="bold",
        color="#6C3483", va="center", zorder=4)
row(-8.6, 4.2, [("classify_and_extract", "七类意图 + 槽位\n输出 city / category"),
                ("retrieve", "调用 build_evidence\n(query, city, category)"),
                ("compose", "读取 citations\n逐句绑定 source_id"),
                ("rag-eval.yml（CI）", "指标退化 >2 个百分点\n阻止合并")], tf=8.4, sf=6.8)
ax.add_patch(FancyArrowPatch((36, -0.4), (36, -3.6), arrowstyle="-|>", mutation_scale=14,
                             lw=1.5, color="#7D3C98", ls="--", zorder=5))

fig.savefig(ROOT / "docs" / "system_architecture.png", dpi=190, bbox_inches="tight", facecolor="white")
print("已生成 docs/system_architecture.png")
