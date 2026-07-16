"""List SMC setups in forming/waiting/ready state."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import config
from core.candle_resample import load_candles_1m_sync
from core.runtime_paths import resolved_database_path
from core.strategy_engine import propose_entry
from core.structure_gate import StructureGate


def main() -> None:
    db = resolved_database_path()
    gate = StructureGate()
    symbols = list(config.default_symbols)

    awaiting_bos: list = []
    awaiting_retest: list = []
    ready_in_zone: list = []
    ready_outside: list = []

    for sym in symbols:
        setup = gate.scan_symbol(db, sym)
        if not setup or setup.state == "none":
            continue
        act = "BUY" if setup.side == "long" else "SELL"
        side_ru = "LONG" if act == "BUY" else "SHORT"
        zlo = zhi = 0.0
        zone_txt = ""
        if setup.zone:
            zlo, zhi = float(setup.zone.low), float(setup.zone.high)
            zone_txt = f"{zlo:.6g}–{zhi:.6g} ({setup.zone.kind})"

        c1m = load_candles_1m_sync(db, sym, limit=5)
        price = float(c1m[-1]["close"]) if c1m else 0.0

        sc = gate.score_symbol(db, sym)
        q = sc.quality_score if sc else 0
        wp = sc.win_probability if sc else 0.0

        row = {
            "symbol": sym,
            "side": side_ru,
            "quality": q,
            "win_p": wp,
            "zone": zone_txt,
            "price": price,
        }

        if setup.state == "await_bos":
            awaiting_bos.append(row)
        elif setup.state == "await_retest":
            awaiting_retest.append(row)
        elif setup.state == "ready":
            mode = "continuation" if setup.checklist.get("continuation") else "retest"
            ent, in_zone, _ = propose_entry(setup, act, price, mode)
            row["entry_mode"] = mode
            row["in_zone"] = in_zone
            row["entry"] = ent
            if in_zone:
                ready_in_zone.append(row)
            else:
                ready_outside.append(row)

    print("=" * 72)
    print(f"SMC SETUPS | {len(symbols)} symbols scanned")
    print("=" * 72)

    def _print_block(title: str, rows: list, extra: str = "") -> None:
        print(f"\n{title} ({len(rows)})")
        if not rows:
            print("  net")
            return
        for r in sorted(rows, key=lambda x: -x["quality"]):
            line = (
                f"  {r['symbol']:12} {r['side']:5} q={r['quality']:3} "
                f"win={r['win_p']:.0%}"
            )
            if r.get("zone"):
                line += f"  zone {r['zone']}"
            if r.get("price"):
                line += f"  now={r['price']:.6g}"
            if extra:
                line += extra.format(**r)
            print(line)

    _print_block("[await BOS] Svipe est, sloma struktury net", awaiting_bos)
    _print_block("[await RETEST] BOS est, zhдем vozvrat v zonu", awaiting_retest)
    _print_block(
        "[READY in zone] Tsena v zone - mozhet uyti alert",
        ready_in_zone,
        "  entry={entry:.6g}",
    )
    _print_block(
        "[READY outside zone] Setup est, tsena vne zony",
        ready_outside,
    )

    print("\n" + "=" * 72)


if __name__ == "__main__":
    main()
