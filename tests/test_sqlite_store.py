import sqlite3

import sqlite_store


def test_schema_initializes_with_wal(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.init_db(db)

    with sqlite3.connect(db) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        schema_version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert schema_version == sqlite_store.SCHEMA_VERSION


def test_snapshot_round_trip_and_atomic_patch(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.write_snapshot("state", {"paused": False, "count": 1}, path=db)
    patched = sqlite_store.update_snapshot(
        "state",
        lambda current: {**current, "paused": True, "count": current["count"] + 1},
        path=db,
    )

    assert patched == {"paused": True, "count": 2}
    assert sqlite_store.read_snapshot("state", path=db) == patched


def test_events_append_and_read_in_insertion_order(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.append_event({"tsUtc": "2026-01-01T00:00:02+00:00", "event": "EXIT"}, path=db)
    sqlite_store.append_event({"tsUtc": "2026-01-01T00:00:01+00:00", "event": "ENTER"}, path=db)

    assert [row["event"] for row in sqlite_store.list_events(path=db)] == ["EXIT", "ENTER"]
    assert [row["event"] for row in sqlite_store.list_events(limit=1, path=db)] == ["ENTER"]


def test_history_upsert_keeps_latest_value(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.upsert_history_item("2026-01-01T00:00:00+00:00", 100.0, 101.0, path=db)
    sqlite_store.upsert_history_item("2026-01-01T00:00:00+00:00", 102.0, 103.0, path=db)

    assert sqlite_store.read_history(path=db)["items"] == [
        {"ts": "2026-01-01T00:00:00+00:00", "price": 102.0, "equity": 103.0}
    ]


def test_compact_retains_recent_events_and_history(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    for idx in range(5):
        sqlite_store.append_event({"tsUtc": f"2026-01-01T00:00:0{idx}+00:00", "event": f"E{idx}"}, path=db)
        sqlite_store.upsert_history_item(f"2026-01-01T00:00:0{idx}+00:00", 100.0 + idx, 1000.0 + idx, path=db)

    result = sqlite_store.compact(event_keep=2, history_keep=3, path=db)

    assert result == {"eventsDeleted": 3, "historyDeleted": 2}
    assert [row["event"] for row in sqlite_store.list_events(path=db)] == ["E3", "E4"]
    assert [row["price"] for row in sqlite_store.read_history(path=db)["items"]] == [102.0, 103.0, 104.0]
