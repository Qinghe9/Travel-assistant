#!/usr/bin/env python3
"""生成 7 类 × 30 条可重复意图评测集（synthetic-template-v1）。"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CITIES = [
    "北京", "上海", "广州", "深圳", "杭州", "成都", "西安", "重庆",
    "南京", "武汉", "长沙", "苏州", "厦门", "青岛", "天津", "昆明",
    "大理", "丽江", "桂林", "三亚", "哈尔滨", "沈阳", "济南", "郑州",
    "合肥", "福州", "南昌", "南宁", "贵阳", "兰州",
]
ORIGINS = ["广州", "深圳", "北京", "上海", "杭州", "成都", "西安", "南京", "武汉", "长沙"]

rows = []
for i, city in enumerate(CITIES):
    rows.append({"query": "%s有什么好玩的景点" % city, "expected_intent": "INT-01",
                 "expected_slots": {"destination": city},
                 "source": "synthetic-template-v1", "type": "destination"})
    days = i % 4 + 2
    rows.append({"query": "帮我规划%d天%s行程" % (days, city),
                 "expected_intent": "INT-02",
                 "expected_slots": {"destination": city, "days": days},
                 "source": "synthetic-template-v1", "type": "itinerary"})
    origin = ORIGINS[i % len(ORIGINS)]
    rows.append({"query": "从%s到%s怎么走" % (origin, city),
                 "expected_intent": "INT-03",
                 "expected_slots": {"origin": origin, "destination_place": city},
                 "source": "synthetic-template-v1", "type": "transport"})
    rows.append({"query": "%s住哪里酒店" % city, "expected_intent": "INT-04",
                 "expected_slots": {"destination": city},
                 "source": "synthetic-template-v1", "type": "hotel"})
    rows.append({"query": "%s有什么特色美食" % city, "expected_intent": "INT-05",
                 "expected_slots": {"destination": city},
                 "source": "synthetic-template-v1", "type": "food"})
    rows.append({"query": "明天%s天气怎么样" % city,
                 "expected_intent": "INT-06",
                 "expected_slots": {"destination": city},
                 "source": "synthetic-template-v1", "type": "weather"})
    chat = ["你好", "谢谢", "你能做什么", "再见", "在吗"][i % 5]
    rows.append({"query": chat, "expected_intent": "INT-07", "expected_slots": {},
                 "source": "synthetic-template-v1", "type": "chitchat"})

out = ROOT / "evals" / "intent_eval.jsonl"
out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
               encoding="utf-8")
print("wrote %s (%d rows)" % (out, len(rows)))
