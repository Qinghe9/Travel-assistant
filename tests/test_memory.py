import sqlite3

from app.agent.store import _merge, profile_hint
from app.agent.sqlite_utils import tune_sqlite


def test_wal_allows_two_connections_to_alternate_writes(tmp_path):
    db = tmp_path / "agent.db"
    first = sqlite3.connect(db, check_same_thread=False)
    second = sqlite3.connect(db, check_same_thread=False)
    tune_sqlite(first)
    tune_sqlite(second)
    first.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
    first.commit()
    for index in range(20):
        conn = first if index % 2 == 0 else second
        conn.execute("INSERT INTO sample(value) VALUES (?)", ("v%d" % index,))
        conn.commit()
    assert first.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert second.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 20
    first.close()
    second.close()


def test_preferences_are_normalized_and_deduplicated():
    merged = _merge(
        {"preferences": ["离景点近", "带娃"]},
        {"preferences": ["靠近景点", "亲子", "带孩子"]},
    )
    assert merged["preferences"] == ["离景点近", "亲子"]
    hint = profile_hint({
        "preferences": ["离景点近", "靠近景点", "带娃", "带孩子"],
    })
    assert hint.count("离景点近") == 1
    assert hint.count("亲子") == 1
