"""Human-readable Russian Telegram copy for trade alerts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _pct(from_px: float, to_px: float) -> str:
    if not from_px:
        return "—"
    return f"{(to_px - from_px) / from_px * 100:+.2f}%"


def _checklist_line(label: str, done: bool, pending: bool = False) -> str:
    if done:
        return f"✅ {label}"
    if pending:
        return f"⏳ {label} — <b>ждём это</b>"
    return f"○ {label}"


def format_trade_alert(
    *,
    symbol: str,
    action: str,
    entry: Optional[float],
    sl: Optional[float],
    tp: Optional[float],
    confidence: float,
    risk: str,
    reasons: List[str],
    setup_quality: int = 0,
    setup_phase: str = "ready",
    win_probability: float = 0.0,
    setup_checklist: Optional[Dict[str, Any]] = None,
    watch_zone_low: Optional[float] = None,
    watch_zone_high: Optional[float] = None,
    current_price: Optional[float] = None,
    entry_mode: str = "none",
    tp_liquidity_source: str = "",
) -> str:
    act = (action or "").upper()
    phase = (setup_phase or "ready").lower()
    quality = int(setup_quality or 0)
    win_p = float(win_probability or 0.0)
    checklist = setup_checklist or {}

    if phase == "forming":
        title = "👀 <b>СЛЕДИ: сетап формируется</b>"
        side = "LONG" if act == "BUY" else "SHORT" if act == "SELL" else act
        verb = f"Пока не входить — следи за {side}, жди retest"
    elif act == "BUY":
        if entry_mode == "continuation":
            title = "🟢 <b>ГОТОВЫЙ СЕТАП: LONG (продолжение)</b>"
            verb = "Импульс без retest — вход только при согласии с риском"
        else:
            title = "🟢 <b>ГОТОВЫЙ СЕТАП: LONG</b>"
            verb = "Можно рассмотреть покупку (Long)"
        side = "LONG"
    elif act == "SELL":
        if entry_mode == "continuation":
            title = "🔴 <b>ГОТОВЫЙ СЕТАП: SHORT (продолжение)</b>"
            verb = "Импульс без retest — вход только при согласии с риском"
        else:
            title = "🔴 <b>ГОТОВЫЙ СЕТАП: SHORT</b>"
            verb = "Можно рассмотреть продажу (Short)"
        side = "SHORT"
    else:
        title = f"📊 <b>{act}</b>"
        verb = act
        side = act

    risk_ru = {
        "Low": "низкий",
        "Medium": "средний",
        "High": "высокий",
        "low": "низкий",
        "medium": "средний",
        "high": "высокий",
    }.get(risk, risk)

    lines = [
        title,
        f"<b>{symbol}</b>" + (f"  ·  {side}" if phase == "forming" else ""),
        "",
    ]

    if quality > 0:
        bar = "█" * (quality // 10) + "░" * (10 - quality // 10)
        lines.append(f"Качество: <b>{quality}/100</b> [{bar}]")
    if win_p > 0:
        label = "Шанс (если подтвердится)" if phase == "forming" else "Оценка шанса"
        lines.append(f"{label}: <b>{win_p:.0%}</b>")

    if phase == "forming":
        setup_state = str(checklist.get("setup_state") or "")
        sweep = bool(checklist.get("liquidity_sweep"))
        bos = bool(checklist.get("bos"))
        retest = bool(checklist.get("retest"))
        lines.extend(["", "<b>Этапы SMC:</b>"])
        lines.append(_checklist_line("Свип ликвидности", sweep))
        lines.append(_checklist_line("BOS (слом структуры)", bos))
        lines.append(
            _checklist_line(
                "Retest зоны (отбой)",
                retest,
                pending=setup_state == "await_retest" and not retest,
            )
        )
        lines.extend(["", "<b>За чем следить:</b>"])
        if watch_zone_low and watch_zone_high and watch_zone_low > 0:
            lines.append(
                f"→ Цена должна вернуться в зону "
                f"<code>{watch_zone_low:.6g}</code> – <code>{watch_zone_high:.6g}</code>"
            )
            if current_price and current_price > 0:
                lines.append(f"→ Сейчас: <code>{current_price:.6g}</code>")
        elif setup_state == "await_bos":
            lines.append("→ Ждём слом структуры (BOS) после свипа")
        elif setup_state == "await_retest":
            lines.append("→ BOS есть — ждём retest зоны или уход без отката (продолжение)")
        else:
            lines.append("→ Ждём retest зоны на графике (жёлтая полоса)")
        lines.append(
            "→ Retest → «ГОТОВЫЙ СЕТАП» (≥72) · без retest → «продолжение» (≥76)"
        )
        lines.append("→ <b>Пока не входить</b>")
    else:
        lines.extend(["", "<b>Что делать:</b>", f"→ {verb}"])
        if phase == "ready" and checklist:
            ctx_lines = []
            zlo = checklist.get("zone_low")
            zhi = checklist.get("zone_high")
            if zlo and zhi and float(zlo) > 0 and float(zhi) > float(zlo):
                ctx_lines.append(
                    f"✅ Зона входа: <code>{float(zlo):.6g}</code> – "
                    f"<code>{float(zhi):.6g}</code>"
                )
            flow_dom = checklist.get("flow_dominance")
            if flow_dom:
                delta = float(checklist.get("flow_delta_pct") or 0)
                dom_ru = {
                    "buyers": "покупатели",
                    "sellers": "продавцы",
                    "neutral": "нейтрально",
                }.get(str(flow_dom), str(flow_dom))
                if checklist.get("flow_aligned"):
                    ctx_lines.append(f"✅ Поток: {dom_ru} ({delta:+.1f}%)")
                else:
                    ctx_lines.append(f"○ Поток: {dom_ru} ({delta:+.1f}%)")
            if checklist.get("book_aligned"):
                imb = checklist.get("book_imbalance", 0)
                ctx_lines.append(f"✅ Стакан: {imb:+.0%} в сторону сделки")
            elif checklist.get("thin_book"):
                ctx_lines.append("⚠️ Тонкий стакан / широкий спред")
            elif checklist.get("book_imbalance") is not None:
                imb = float(checklist.get("book_imbalance") or 0)
                ctx_lines.append(f"○ Стакан: {imb:+.0%}")
            if checklist.get("oi_available"):
                ch = float(checklist.get("oi_change_pct") or 0)
                if checklist.get("oi_aligned"):
                    ctx_lines.append(f"✅ OI: {ch:+.1f}% за 4ч")
                elif checklist.get("oi_divergence"):
                    ctx_lines.append(f"⚠️ OI против: {ch:+.1f}%")
                else:
                    ctx_lines.append(f"○ OI: {ch:+.1f}%")
            disp = checklist.get("displacement_pct")
            room = checklist.get("tp_room_pct")
            if disp is not None and room is not None:
                ctx_lines.append(
                    f"○ Потенциал: импульс {float(disp):.2f}% · до TP {float(room):.2f}%"
                )
            if ctx_lines:
                lines.extend(["", "<b>Рынок:</b>"])
                lines.extend(ctx_lines)
            exit_plan = checklist.get("exit_plan")
            if exit_plan:
                lines.extend(["", "<b>План выхода:</b>", f"→ {exit_plan}"])

    px = entry or 0.0
    if px > 0:
        lines.append(f"→ Вход: <code>{px:.6g}</code>")
    if sl and sl > 0 and px > 0:
        lines.append(f"→ Стоп (SL): <code>{sl:.6g}</code> ({_pct(px, sl)})")
    elif sl and sl > 0 and phase == "ready":
        lines.append(f"→ Стоп (SL): <code>{sl:.6g}</code>")
    if tp and tp > 0 and px > 0:
        tp_line = f"→ Цель (TP): <code>{tp:.6g}</code> ({_pct(px, tp)})"
        if tp_liquidity_source:
            from core.liquidity_targets import liquidity_kind_label

            tp_line += f" · <i>{liquidity_kind_label(tp_liquidity_source)}</i>"
        lines.append(tp_line)
    elif tp and tp > 0 and phase == "ready":
        lines.append(f"→ Цель (TP): <code>{tp:.6g}</code>")
    if sl and tp and px > 0:
        risk_abs = abs(px - sl)
        reward_abs = abs(tp - px)
        if risk_abs > 0:
            rr = reward_abs / risk_abs
            lines.append(f"→ R:R ≈ 1:{rr:.1f}")

    lines.extend(
        [
            "",
            f"Уверенность агентов: <b>{confidence:.0%}</b>  ·  Риск: <b>{risk_ru}</b>",
        ]
    )

    short_reasons = [r for r in (reasons or []) if r][:2]
    if short_reasons:
        lines.append("")
        lines.append("<b>Контекст:</b>")
        for r in short_reasons:
            lines.append(f"• {r}")

    if phase == "forming":
        lines.append("")
        lines.append(
            "<i>Это наблюдение, не сигнал на вход. "
            "Готовый = retest (≥72) или продолжение без retest (≥76).</i>"
        )
    elif entry_mode == "continuation":
        lines.append("")
        lines.append(
            "<i>Цена ушла без retest. Порог выше, SL ближе — решение за тобой.</i>"
        )
    else:
        lines.append("")
        lines.append(
            "<i>Решение за тобой. Бот ведёт paper-журнал и учится на исходах.</i>"
        )
    return "\n".join(lines)


def format_paper_partial_alert(
    *,
    symbol: str,
    action: str,
    entry: float,
    tp1: float,
    partial_pnl_pct: float,
    tp: float,
    partial_size: float = 0.5,
) -> str:
    act = (action or "").upper()
    side = "LONG" if act == "BUY" else "SHORT" if act == "SELL" else act
    pct = int(round(partial_size * 100))
    lines = [
        "💰 <b>Paper: частичный TP (TP1)</b>",
        f"<b>{symbol}</b> · {side}",
        "",
        f"→ Закрыто <b>{pct}%</b> позиции на 1.5R",
        f"→ Вход: <code>{entry:.6g}</code>",
        f"→ TP1: <code>{tp1:.6g}</code>  ·  P/L leg: <b>{partial_pnl_pct:+.2f}%</b>",
        f"→ Runner TP (ликвидность): <code>{tp:.6g}</code>",
        "",
        "<i>Остаток позиции — SL на breakeven, цель на полной ликвидности.</i>",
    ]
    return "\n".join(lines)


def format_paper_outcome_alert(
    *,
    symbol: str,
    action: str,
    exit_reason: str,
    entry: float,
    exit_price: float,
    pnl_pct: float,
    sl: float,
    tp: float,
    had_partial: bool = False,
) -> str:
    act = (action or "").upper()
    reason = (exit_reason or "").lower()
    if reason == "tp":
        title = "✅ <b>Paper: TAKE PROFIT</b>"
        detail = "Цель (ликвидность) достигнута" + (" · после partial" if had_partial else "")
    elif reason == "partial_tp":
        title = "💰 <b>Paper: частичный TP</b>"
        detail = "Частичная фиксация"
    elif reason == "sl":
        title = "🛑 <b>Paper: STOP LOSS</b>"
        detail = "Breakeven / стоп" if had_partial else "Стоп сработал"
    else:
        title = "⏱️ <b>Paper: выход по времени</b>"
        detail = "Максимальное время удержания" + (" · blended" if had_partial else "")

    side = "LONG" if act == "BUY" else "SHORT" if act == "SELL" else act
    lines = [
        title,
        f"<b>{symbol}</b> · {side}",
        "",
        f"→ {detail}",
        f"→ Вход: <code>{entry:.6g}</code>",
        f"→ Выход: <code>{exit_price:.6g}</code>",
        f"→ P/L: <b>{pnl_pct:+.2f}%</b> (paper, с комиссией)",
        "",
        f"SL был: <code>{sl:.6g}</code>  ·  TP был: <code>{tp:.6g}</code>",
        "",
        "<i>Это paper-сделка бота для обучения. Решение по реальному входу — за тобой.</i>",
    ]
    return "\n".join(lines)


def format_volume_spike_alert(
    *,
    symbol: str,
    ratio: float,
    price: float,
    price_change_pct: float,
    direction: str,
    setup_state: str = "",
    setup_side: str = "",
    zone_low: Optional[float] = None,
    zone_high: Optional[float] = None,
    quality: int = 0,
    trade_hint: str = "",
    candle_range_pct: float = 0.0,
) -> str:
    dir_ru = {"up": "покупатели", "down": "продавцы", "neutral": "нейтрально"}.get(
        direction, direction
    )
    chg_sign = f"{price_change_pct:+.2f}%"
    lines = [
        "🔥 <b>СИЛЬНЫЙ ИМПУЛЬС ОБЪЁМА</b>",
        f"<b>{symbol}</b>",
        "",
    ]
    if trade_hint:
        lines.append(f"→ {trade_hint}")
    lines.extend(
        [
            f"→ Объём: <b>{ratio:.1f}x</b> от среднего за 20 мин",
            f"→ Свеча: {chg_sign} · диапазон {candle_range_pct:.2f}% · {dir_ru}",
            f"→ Цена: <code>{price:.6g}</code>",
        ]
    )
    if setup_state and setup_state != "none":
        side = (setup_side or "").lower()
        side_ru = "LONG" if side == "long" else "SHORT" if side == "short" else ""
        state_ru = {
            "await_bos": "формируется — ждём BOS",
            "await_retest": "ждём retest в зону",
            "ready": "сетап READY",
        }.get(setup_state, setup_state)
        lines.extend(["", "<b>SMC контекст:</b>"])
        if side_ru:
            lines.append(f"→ {side_ru}: {state_ru}")
        else:
            lines.append(f"→ {state_ru}")
        if quality > 0:
            lines.append(f"→ Качество сетапа: <b>{quality}/100</b>")
        if zone_low and zone_high and zone_low > 0 and zone_high > zone_low:
            lines.append(
                f"→ Зона: <code>{zone_low:.6g}</code> – <code>{zone_high:.6g}</code>"
            )
    lines.extend(
        [
            "",
            "<i>Сильный импульс на активном SMC-сетапе. "
            "Жди retest/READY — вход только по полной стратегии.</i>",
        ]
    )
    return "\n".join(lines)


def format_risk_alert(
    *,
    symbol: str,
    action: str,
    confidence: float,
    reasons: List[str],
) -> str:
    lines = [
        "⚠️ <b>ВНИМАНИЕ: повышенный риск</b>",
        f"<b>{symbol}</b>",
        "",
        "<b>Что делать:</b>",
        "→ Закрой или уменьши позицию, если ты в рынке",
        "→ Не открывай новые сделки по этой монете",
        "",
        f"Уверенность: <b>{confidence:.0%}</b>",
    ]
    for r in (reasons or [])[:2]:
        lines.append(f"• {r}")
    return "\n".join(lines)
