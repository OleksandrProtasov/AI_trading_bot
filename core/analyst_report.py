"""Daily analyst report: paper journal stats + setup activity."""
from __future__ import annotations

from calendar import timegm
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from core.database import Database


async def build_analyst_report(db: "Database") -> str:
    since_ts = timegm((datetime.utcnow() - timedelta(hours=24)).utctimetuple())
    summary: Dict[str, Any] = await db.get_aggregated_outcomes_summary(since_ts)

    lines = [
        "📊 <b>Research-отчёт за 24ч</b>",
        "",
        "<b>Paper-журнал (локально):</b>",
    ]

    overall = summary.get("overall") or {}
    total = int(overall.get("total_evaluated") or 0)
    hit = overall.get("overall_hit_rate")
    pending = int(summary.get("pending_horizon") or 0)

    if total > 0 and hit is not None:
        lines.append(f"• Закрыто сетапов: <b>{total}</b>")
        lines.append(f"• Win rate: <b>{float(hit):.0%}</b>")
    else:
        lines.append("• Пока нет закрытых paper-сделок за сутки")

    lines.append(f"• В ожидании оценки: <b>{pending}</b>")

    by_action = summary.get("by_action") or []
    if by_action:
        lines.append("")
        lines.append("<b>По направлению:</b>")
        for row in by_action:
            act = row.get("action", "?")
            n = int(row.get("n") or 0)
            avg = float(row.get("avg_ret") or 0.0)
            hr = float(row.get("hit_rate") or 0.0)
            label = "LONG" if act == "BUY" else "SHORT" if act == "SELL" else act
            lines.append(
                f"• {label}: {n} шт, WR {hr:.0%}, ср. {avg:+.2f}%"
            )

    lines.extend(
        [
            "",
            "<b>Режим:</b> Analyst — присылаю готовые сетапы (≥72/100) "
            "и «следи» при формировании.",
            "",
            "<i>Бот учится на paper-исходах и подстраивает веса.</i>",
        ]
    )
    return "\n".join(lines)
