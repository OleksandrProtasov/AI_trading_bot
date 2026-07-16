"""Fetch liquid Binance USDT spot pairs by 24h quote volume."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import List, Set

BINANCE_TICKER_24H = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

# Leveraged / synthetic tickers we skip for spot SMC.
_SKIP_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_SKIP_RE = re.compile(r"^(1000|1000000)")
_ASCII_USDT = re.compile(r"^[A-Z0-9]{2,16}USDT$")


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "tradingBot-universe/1.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _trading_usdt_symbols() -> Set[str]:
    data = _get_json(BINANCE_EXCHANGE_INFO)
    out: Set[str] = set()
    for sym in data.get("symbols", []):
        if sym.get("status") != "TRADING":
            continue
        if sym.get("quoteAsset") != "USDT":
            continue
        if sym.get("isSpotTradingAllowed") is False:
            continue
        out.add(str(sym.get("symbol", "")).upper())
    return out


def fetch_top_usdt_symbols(
    n: int = 100,
    *,
    exclude_stable_bases: bool = True,
    require_futures_perp: bool = False,
    futures_pool: Set[str] | None = None,
) -> List[str]:
    """Top *n* USDT spot pairs by 24h quote volume."""
    allowed = _trading_usdt_symbols()
    futures_ok: Set[str] | None = None
    if require_futures_perp:
        if futures_pool is not None:
            futures_ok = futures_pool
        else:
            from core.open_interest import fetch_futures_usdt_symbols

            futures_ok = fetch_futures_usdt_symbols()
    tickers = _get_json(BINANCE_TICKER_24H)
    if not isinstance(tickers, list):
        return []

    stable_bases = {
        "USDT", "USDC", "BUSD", "DAI", "TUSD", "FDUSD", "EUR", "GBP",
        "USD1", "RLUSD", "USDE", "PYUSD", "AEUR", "PAXG", "XAUT",
    }
    ranked: list[tuple[float, str]] = []

    for row in tickers:
        sym = str(row.get("symbol", "")).upper()
        if sym not in allowed or not sym.endswith("USDT"):
            continue
        if any(sym.endswith(s) for s in _SKIP_SUFFIXES):
            continue
        if _SKIP_RE.match(sym):
            continue
        if not _ASCII_USDT.match(sym):
            continue
        base = sym[:-4]
        if exclude_stable_bases and base in stable_bases:
            continue
        try:
            qv = float(row.get("quoteVolume") or 0.0)
        except (TypeError, ValueError):
            qv = 0.0
        if qv <= 0:
            continue
        ranked.append((qv, sym))

    ranked.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: List[str] = []
    for _, sym in ranked:
        if sym in seen:
            continue
        if futures_ok is not None and sym not in futures_ok:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= n:
            break
    return out


def format_trading_symbols_py(symbols: List[str]) -> str:
    lines = [
        '"""Default Binance USDT pairs — top by 24h quote volume."""',
        "from __future__ import annotations",
        "",
        "TRADING_SYMBOLS: tuple[str, ...] = (",
    ]
    for sym in symbols:
        lines.append(f'    "{sym}",')
    lines.append(")")
    lines.append("")
    return "\n".join(lines)
