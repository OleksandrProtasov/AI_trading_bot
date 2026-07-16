from core.order_flow import analyze_trade_flow


def _trade(price: float, qty: float, is_buyer_maker: bool):
    return {"price": price, "quantity": qty, "is_buyer_maker": is_buyer_maker}


def test_buyers_dominant_for_long():
    trades = [_trade(100, 1, False)] * 12 + [_trade(100, 0.2, True)] * 3
    m = analyze_trade_flow(trades, "BUY", min_trades=10, min_delta_pct=0.08)
    assert m.trade_count == 15
    assert m.dominance == "buyers"
    assert m.flow_aligned is True
    assert m.delta_pct > 0


def test_sellers_dominant_for_short():
    trades = [_trade(100, 1, True)] * 12 + [_trade(100, 0.2, False)] * 3
    m = analyze_trade_flow(trades, "SELL", min_trades=10, min_delta_pct=0.08)
    assert m.dominance == "sellers"
    assert m.flow_aligned is True
    assert m.delta_pct < 0


def test_flow_against_long_blocks_alignment():
    trades = [_trade(100, 1, True)] * 12
    m = analyze_trade_flow(trades, "BUY", min_trades=10, min_delta_pct=0.08)
    assert m.flow_aligned is False
    assert m.dominance == "sellers"


def test_insufficient_trades_returns_empty_metrics():
    trades = [_trade(100, 1, False)] * 5
    m = analyze_trade_flow(trades, "BUY", min_trades=10)
    assert m.trade_count == 5
    assert m.delta_pct == 0.0
    assert m.flow_aligned is False
