"""Quick market test: structure gate + replay snapshot for one symbol."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone

from core.candle_resample import load_candles_1m_sync, resample_ohlc
from core.runtime_paths import resolved_database_path
from core.structure_gate import StructureGate
from core.smc_retest import StructureSetupStore


def main() -> None:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT").upper()
    db = resolved_database_path()
    conn = sqlite3.connect(db)

    print("=" * 60)
    print(f"MARKET TEST: {symbol}")
    print(f"DB: {db}")
    print("=" * 60)

    row = conn.execute(
        """
        SELECT MAX(timestamp), MIN(timestamp), COUNT(*)
        FROM candles WHERE symbol=? AND timeframe='1m'
        """,
        (symbol,),
    ).fetchone()
    if not row or not row[0]:
        print("NO CANDLES for symbol")
        sys.exit(1)
    latest, earliest, n = row
    print(
        f"Candles 1m: {n} rows | "
        f"{datetime.fromtimestamp(earliest, tz=timezone.utc):%Y-%m-%d} .. "
        f"{datetime.fromtimestamp(latest, tz=timezone.utc):%Y-%m-%d %H:%M UTC}"
    )

    sig5 = conn.execute(
        "SELECT COUNT(*) FROM signals WHERE symbol=? AND timestamp >= ?",
        (symbol, latest - 300),
    ).fetchone()[0]
    agg5 = conn.execute(
        """
        SELECT COUNT(*) FROM signals
        WHERE symbol=? AND agent_type='aggregator' AND timestamp >= ?
        """,
        (symbol, latest - 3600),
    ).fetchone()[0]
    print(f"Signals last 5m: {sig5} | Aggregator last 1h: {agg5}")

    last_agg = conn.execute(
        """
        SELECT timestamp, signal_type, data FROM signals
        WHERE symbol=? AND agent_type='aggregator'
        ORDER BY timestamp DESC LIMIT 3
        """,
        (symbol,),
    ).fetchall()
    print("\nLast aggregator signals:")
    for ts, st, data in last_agg:
        try:
            payload = json.loads(data) if data else {}
        except Exception:
            payload = {}
        act = payload.get("action", st)
        conf = payload.get("confidence", "?")
        reasons = payload.get("reasons") or []
        struct = [r for r in reasons if "Structure" in str(r) or "retest" in str(r).lower()]
        print(
            f"  {datetime.fromtimestamp(ts, tz=timezone.utc):%H:%M:%S} "
            f"{act} conf={conf}"
        )
        if struct:
            print(f"    -> {struct[0][:120]}")
        elif reasons:
            print(f"    -> {str(reasons[-1])[:120]}")

    gate = StructureGate()
    setup = gate.scan_symbol(db, symbol)
    print("\nStructure scan:")
    if setup:
        print(f"  state={setup.state} side={setup.side} trend={setup.trend}")
        print(f"  zone={setup.zone.kind} [{setup.zone.low:.4f}-{setup.zone.high:.4f}]")
        print(f"  checklist={setup.checklist}")
    else:
        print("  no active setup")

    px_row = conn.execute(
        """
        SELECT close FROM candles
        WHERE symbol=? AND timeframe='1m'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    price = float(px_row[0]) if px_row else None

    for action in ("BUY", "SELL"):
        res = gate.evaluate(
            db_path=db,
            symbol=symbol,
            action=action,
            entry_price=price,
            enabled=True,
        )
        print(f"\nGate {action} @ {price}:")
        print(f"  allowed={res.allowed}")
        print(f"  reason={res.reason[:200] if res.reason else '-'}")
        if res.sl and res.tp:
            print(f"  SL={res.sl:.4f} TP={res.tp:.4f} RR=1:{res.rr_ratio:.1f}")

    conn.close()
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
