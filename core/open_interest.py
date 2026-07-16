"""Binance USD-M futures open interest fetch helpers."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Set

BINANCE_FAPI_OI = "https://fapi.binance.com/fapi/v1/openInterest"
BINANCE_FAPI_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"


def _get_json(url: str, params: Optional[Dict[str, str]] = None) -> object:
    qs = urllib.parse.urlencode(params or {})
    full = f"{url}?{qs}" if qs else url
    req = urllib.request.Request(full, headers={"User-Agent": "tradingBot-oi/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_futures_usdt_symbols() -> Set[str]:
    try:
        data = _get_json(BINANCE_FAPI_EXCHANGE_INFO)
    except Exception:
        return set()
    out: Set[str] = set()
    for sym in data.get("symbols", []):
        if sym.get("status") != "TRADING":
            continue
        if sym.get("contractType") != "PERPETUAL":
            continue
        if sym.get("quoteAsset") != "USDT":
            continue
        out.add(str(sym.get("symbol", "")).upper())
    return out


def fetch_open_interest(symbol: str) -> Optional[float]:
    sym = symbol.upper()
    try:
        data = _get_json(BINANCE_FAPI_OI, {"symbol": sym})
        oi = float(data.get("openInterest") or 0)
        return oi if oi > 0 else None
    except urllib.error.HTTPError:
        return None
    except Exception:
        return None


def fetch_open_interest_batch(
    symbols: List[str],
    *,
    allowed: Optional[Set[str]] = None,
) -> Dict[str, float]:
    """Return {symbol: oi} for symbols with active USDT perps."""
    allowed_set = allowed or fetch_futures_usdt_symbols()
    out: Dict[str, float] = {}
    for sym in symbols:
        s = sym.upper()
        if s not in allowed_set:
            continue
        oi = fetch_open_interest(s)
        if oi is not None:
            out[s] = oi
    return out
