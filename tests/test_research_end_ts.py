import sqlite3
import time

from core.research_end_ts import latest_data_timestamp, resolve_research_end_ts


def test_resolve_research_end_ts_caps_future_explicit(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE signals (timestamp INTEGER)")
    conn.execute("CREATE TABLE candles (timestamp INTEGER)")
    conn.execute("INSERT INTO signals VALUES (?)", (1_000_000,))
    conn.execute("INSERT INTO candles VALUES (?)", (1_000_100,))
    conn.commit()
    conn.close()

    capped = resolve_research_end_ts(str(db), horizon_minutes=30, explicit=int(time.time()))
    assert capped <= 1_000_100 - 30 * 60


def test_latest_data_timestamp_returns_max(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE signals (timestamp INTEGER)")
    conn.execute("CREATE TABLE candles (timestamp INTEGER)")
    conn.execute("INSERT INTO signals VALUES (?)", (500,))
    conn.execute("INSERT INTO candles VALUES (?)", (900,))
    conn.commit()
    conn.close()
    assert latest_data_timestamp(str(db)) == 900
