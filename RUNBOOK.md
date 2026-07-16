# Trading Bot Runbook

This file contains practical commands for routine research runs.

## 1) Quick sanity checks

```powershell
pytest -q
python bot_activity.py
```

## 2) Single replay backtest (fixed EV params)

```powershell
python historical_replay_backtest.py `
  --hours 720 `
  --horizon-minutes 30 `
  --recent-window-sec 120 `
  --min-score 0.26 `
  --min-margin 0.08 `
  --dedup-sec 40 `
  --min-confidence 0.4 `
  --slippage-bps 3 `
  --ev-buffer-bps 8 `
  --ev-confidence-mult 18 `
  --ev-margin-mult 22 `
  --ev-source-mult 3 `
  --ev-bearish-penalty-mult 6 `
  --ev-emergency-penalty-mult 4 `
  --ev-conflict-penalty-mult 25
```

## 3) EV grid optimization (compact search)

```powershell
python optimize_replay_ev.py `
  --hours 720 `
  --top 5 `
  --ev-buffers 8 `
  --ev-confidence-mults 18,20 `
  --ev-margin-mults 20,22 `
  --ev-source-mults 2,3 `
  --ev-bearish-penalty-mults 6 `
  --ev-emergency-penalty-mults 4 `
  --ev-conflict-penalty-mults 25
```

## 4) Walk-forward with adaptive step search

Use `--max-runtime-sec` to keep long runs bounded.

```powershell
python walk_forward_replay.py `
  --end-ts 1777212369 `
  --window-hours 720 `
  --windows 3 `
  --auto-step-hours 168,72,48,24,12 `
  --recent-window-sec 120 `
  --min-score 0.26 `
  --min-margin 0.08 `
  --dedup-sec 40 `
  --min-confidence 0.4 `
  --min-trades-per-window 20 `
  --min-active-windows 3 `
  --max-runtime-sec 1800 `
  --ev-buffers 8 `
  --ev-confidence-mults 18 `
  --ev-margin-mults 22 `
  --ev-source-mults 3 `
  --ev-bearish-penalty-mults 6 `
  --ev-emergency-penalty-mults 4 `
  --ev-conflict-penalty-mults 25 `
  --top 1
```

## 5) Tail walk-forward (recent 7d windows)

Aligned with live filters (`min-confidence=0.58`). Bootstrap no longer lowers confidence.

```powershell
python daily_research.py --tail-wf --max-runtime-sec 900
```

Or directly:

```powershell
python walk_forward_replay.py `
  --window-hours 168 `
  --windows 4 `
  --auto-step-hours 48,24,12 `
  --min-confidence 0.58 `
  --min-score 0.35 `
  --min-margin 0.12 `
  --min-trades-per-window 5 `
  --min-active-windows 2 `
  --max-runtime-sec 900
```

## 6) Suggested daily loop

1. Run `pytest -q`.
2. Run a compact WF (`windows=3`, adaptive step).
3. If `best` is invalid (`active_windows<min`), lower strictness:
   - reduce `min_trades_per_window` (20 -> 15 -> 10),
   - or shorten `window-hours`.
4. Promote only configs that keep drawdown controlled across windows.

## 7) One-command daily research

```powershell
python daily_research.py `
  --max-runtime-sec 1800 `
  --keep 30 `
  --promote-min-score-delta 0.2 `
  --promote-max-drawdown-pct 12 `
  --promote-on-equal
```

History is stored at `reports/daily_research_history.json`.
Latest full WF output is stored at `reports/latest_wf.json`.
Best EV params for quick copy are stored at `reports/best_params.env`.
Auto-promoted params are stored at `reports/promoted_best.env`.

## 8) Apply promoted params to live `.env`

Dry-run:

```powershell
python apply_promoted_env.py --dry-run
```

Apply (only `AGG_EV_*` keys are touched):

```powershell
python apply_promoted_env.py
```

API snapshot for dashboard:

- `GET /api/research/summary`

## 9) Agent edge report (who helps / hurts)

```powershell
python agent_edge_report.py --hours 720
```

Use this to decide which agents to trust before changing weights.

```powershell
python sync_agent_weights.py --hours 720
```

Restart bot/API after sync so aggregator loads `reports/agent_weights.json`.

## 10) BTC trend filter (alt BUY/SELL gate)

When BTC 30m return is below threshold (default `-0.08%`), altcoin `BUY` is blocked.
When BTC is strongly up, altcoin `SELL` is blocked.
Configure via `.env`: `AGG_BTC_TREND_*`.

## 11) R:R gate (min expected move)

Requires expected edge >= fees + slippage + buffer + **15 bps** (0.15%) profit floor:

```env
AGG_RR_GATE_ENABLED=1
AGG_RR_MIN_PROFIT_BPS=15
```

## 12) Outcome-based edge calibration

```powershell
python sync_edge_calibration.py --hours 720 --min-samples 30
```

Writes `reports/edge_calibration.json`. Restart bot after sync.
Add to daily loop after `sync_agent_weights.py`.

## 13) Backfill candles (when bot was offline)

```powershell
python backfill_candles.py --days 90
```

Fills gaps from Binance REST into `crypto_analytics.db`.

## 14) Train ML signal-quality model

```powershell
pip install scikit-learn joblib
python train_signal_model.py --min-samples 80
```

Writes `reports/signal_model.joblib`. Restart bot to load ML gate (`AGG_ML_GATE_ENABLED=1`).

## 15) Start full stack

```powershell
.\START.bat
# or separately:
python web/api.py
python web/dashboard_enhanced.py
python main.py
```

Dashboard: http://127.0.0.1:8000 | API: http://127.0.0.1:8001

## 16) SMC structure gate (sweep → BOS → retest)

Requires HTF trend alignment, blocks mid-range entries, waits for retest of OB/FVG.

```env
AGG_STRUCTURE_GATE_ENABLED=1
AGG_STRUCTURE_MIN_RR=3.0
```

Pipeline: `core/structure_gate.py`, `core/smc_retest.py`. Restart bot after changes.
