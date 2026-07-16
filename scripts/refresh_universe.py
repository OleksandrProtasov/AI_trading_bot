"""Refresh core/trading_symbols.py from Binance top-N USDT volume."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.binance_universe import fetch_top_usdt_symbols, format_trading_symbols_py


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("-n", "--count", type=int, default=100)
    p.add_argument(
        "--require-futures",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep only symbols with USDT-M perpetual (for OI filter)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    symbols = fetch_top_usdt_symbols(
        args.count, require_futures_perp=bool(args.require_futures)
    )
    if not symbols:
        print("ERROR: no symbols fetched", file=sys.stderr)
        sys.exit(1)

    print(f"Fetched {len(symbols)} symbols (top by 24h USDT volume)")
    for i, sym in enumerate(symbols[:10], 1):
        print(f"  {i}. {sym}")
    print(f"  ... ({len(symbols)} total)")

    if args.dry_run:
        return

    target = ROOT / "core" / "trading_symbols.py"
    target.write_text(format_trading_symbols_py(symbols), encoding="utf-8")
    print(f"Wrote {target}")


if __name__ == "__main__":
    main()
