"""PNG trade chart for Telegram — same LTF candles the structure gate uses."""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _format_price(price: float) -> str:
    p = float(price)
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    if p >= 0.01:
        return f"{p:.6f}"
    return f"{p:.8f}"


def _sanitize_ohlc(candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for c in candles:
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        if h < l:
            h, l = l, h
        h = max(h, o, cl)
        l = min(l, o, cl)
        if h <= 0 or l <= 0:
            continue
        out.append(
            {
                "timestamp": int(c["timestamp"]),
                "open": o,
                "high": h,
                "low": l,
                "close": cl,
            }
        )
    return out


def _chart_y_range(
    ohlc: List[Dict[str, Any]],
    *,
    entry: float,
    sl: Optional[float],
    tp: Optional[float],
    zone_low: Optional[float],
    zone_high: Optional[float],
    max_span_pct: float = 6.0,
) -> Tuple[float, float, bool]:
    """Y limits focused on structure; flag if TP is off-screen."""
    lo = min(float(c["low"]) for c in ohlc)
    hi = max(float(c["high"]) for c in ohlc)
    anchors = [lo, hi, entry]
    for v in (sl, tp, zone_low, zone_high):
        if v and float(v) > 0:
            anchors.append(float(v))
    mn, mx = min(anchors), max(anchors)
    span = mx - mn
    tp_off = False
    max_span = entry * (max_span_pct / 100.0) if entry > 0 else span
    if max_span > 0 and span > max_span:
        core_lo = min(
            x
            for x in (entry, zone_low or entry, sl or entry, lo)
            if x and x > 0
        )
        core_hi = max(
            x
            for x in (entry, zone_high or entry, hi)
            if x and x > 0
        )
        if tp and float(tp) > 0:
            tp_dist = abs(float(tp) - entry) / entry * 100.0 if entry > 0 else 99.0
            if tp_dist <= max_span_pct * 0.85:
                core_hi = max(core_hi, float(tp)) if float(tp) > entry else core_hi
                core_lo = min(core_lo, float(tp)) if float(tp) < entry else core_lo
            else:
                tp_off = True
        mid = (core_lo + core_hi) / 2.0
        mn = mid - max_span / 2.0
        mx = mid + max_span / 2.0
        if tp and float(tp) > 0 and (float(tp) < mn or float(tp) > mx):
            tp_off = True
    pad = (mx - mn) * 0.06 if mx > mn else entry * 0.002
    return mn - pad, mx + pad, tp_off


def build_trade_chart_bytes(
    db_path: str,
    symbol: str,
    *,
    action: str,
    entry: Optional[float] = None,
    sl: Optional[float] = None,
    tp: Optional[float] = None,
    zone_low: Optional[float] = None,
    zone_high: Optional[float] = None,
    bar_minutes: int = 15,
    bars: int = 96,
    tp_source: str = "",
) -> Optional[bytes]:
    """Return PNG bytes or None. Defaults: 15m × 96 bars (same LTF as structure gate)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError:
        return None

    from core.candle_resample import load_candles_1m_sync, resample_ohlc

    c1m = load_candles_1m_sync(db_path, symbol, limit=max(1200, bars * bar_minutes * 3))
    if len(c1m) < 30:
        return None

    ohlc = _sanitize_ohlc(resample_ohlc(c1m, bar_minutes * 60))
    if len(ohlc) < 10:
        return None
    ohlc = ohlc[-bars:]

    entry_v = float(entry) if entry and entry > 0 else float(ohlc[-1]["close"])
    y_min, y_max, tp_off_chart = _chart_y_range(
        ohlc,
        entry=entry_v,
        sl=sl,
        tp=tp,
        zone_low=zone_low,
        zone_high=zone_high,
    )

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=130)
    fig.patch.set_facecolor("#0f1117")
    ax.set_facecolor("#0f1117")

    times = [
        datetime.fromtimestamp(int(c["timestamp"]), tz=timezone.utc) for c in ohlc
    ]
    xs = mdates.date2num(times)
    if len(xs) > 1:
        width = (xs[1] - xs[0]) * 0.65
    else:
        width = bar_minutes / (24 * 60)

    up = "#26a69a"
    down = "#ef5350"
    for x, c in zip(xs, ohlc):
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl = float(c["close"])
        color = up if cl >= o else down
        ax.plot([x, x], [l, h], color=color, linewidth=1.0, solid_capstyle="round")
        body_low = min(o, cl)
        body_h = max(abs(cl - o), (h - l) * 0.04)
        ax.add_patch(
            Rectangle(
                (x - width / 2, body_low),
                width,
                body_h,
                facecolor=color,
                edgecolor=color,
            )
        )

    if zone_low and zone_high and zone_low > 0 and zone_high > zone_low:
        ax.axhspan(
            float(zone_low),
            float(zone_high),
            color="#ffd54f",
            alpha=0.20,
            label="Retest zone",
            zorder=0,
        )

    ax.axhline(entry_v, color="#ffd54f", linewidth=1.5, linestyle="-", label="Entry", zorder=3)
    if sl and sl > 0:
        ax.axhline(float(sl), color="#ef5350", linewidth=1.3, linestyle="--", label="SL", zorder=3)
    if tp and tp > 0:
        tp_label = "TP"
        if tp_source:
            tp_label = f"TP ({tp_source})"
        ax.axhline(
            float(tp),
            color="#66bb6a",
            linewidth=1.3,
            linestyle="--",
            label=tp_label,
            zorder=3,
        )
        if tp_off_chart:
            arrow_y = y_max if float(tp) > entry_v else y_min
            ax.annotate(
                f"TP {_format_price(float(tp))}",
                xy=(xs[-1], float(tp)),
                xytext=(xs[-1], arrow_y),
                color="#66bb6a",
                fontsize=7,
                ha="right",
                va="bottom" if float(tp) > entry_v else "top",
                arrowprops=dict(arrowstyle="->", color="#66bb6a", lw=0.8),
            )

    act = (action or "").upper()
    side = "LONG" if act == "BUY" else "SHORT" if act == "SELL" else act
    ax.set_title(
        f"{symbol}  ·  {side}  ·  {bar_minutes}m (LTF структуры)",
        color="#e8eaed",
        fontsize=10,
        pad=8,
    )
    ax.set_ylim(y_min, y_max)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
    ax.tick_params(colors="#9aa0a6", labelsize=7)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _format_price(v)))
    ax.grid(True, alpha=0.12, color="#9aa0a6")
    for spine in ax.spines.values():
        spine.set_color("#3c4043")
    ax.legend(
        loc="upper left",
        fontsize=7,
        facecolor="#1a1d24",
        edgecolor="#3c4043",
        labelcolor="#e8eaed",
    )
    fig.autofmt_xdate(rotation=20)
    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.read()
