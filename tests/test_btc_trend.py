from core.btc_trend import blocks_alt_buy, blocks_alt_sell, classify_trend, is_alt_symbol


def test_classify_trend_down():
    assert classify_trend(-0.2, down_threshold_pct=-0.08) == "down"


def test_blocks_alt_buy_on_btc_down():
    assert blocks_alt_buy("ETHUSDT", "BUY", "down", enabled=True)
    assert not blocks_alt_buy("BTCUSDT", "BUY", "down", enabled=True)
    assert not blocks_alt_buy("ETHUSDT", "BUY", "up", enabled=True)


def test_is_alt_symbol():
    assert is_alt_symbol("SOLUSDT")
    assert not is_alt_symbol("BTCUSDT")


def test_blocks_alt_sell_on_btc_up():
    assert blocks_alt_sell("ETHUSDT", "SELL", "up", enabled=True)
    assert not blocks_alt_sell("BTCUSDT", "SELL", "up", enabled=True)
    assert not blocks_alt_sell("ETHUSDT", "SELL", "down", enabled=True)
