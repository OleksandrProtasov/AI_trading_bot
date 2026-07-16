from core.volume_spike import detect_volume_spike, evaluate_tradable_volume_spike


def _c(vol, o, h, l, c):
    return {"volume": vol, "open": o, "high": h, "low": l, "close": c}


class _Zone:
    def __init__(self, lo, hi):
        self.low = lo
        self.high = hi


class _Setup:
    def __init__(self, side, state):
        self.side = side
        self.state = state
        self.zone = _Zone(100, 101)


def test_no_spike_when_flat_volume():
    candles = [_c(100, 1, 1, 1, 1) for _ in range(25)]
    assert detect_volume_spike(candles, min_ratio=5.0) is None


def test_tradable_requires_setup_and_alignment():
    candles = [_c(100, 1, 1, 1, 1) for _ in range(24)]
    candles.append(_c(600, 1, 1.01, 0.99, 1.008))  # +0.8% up, wide range
    setup = _Setup("long", "await_bos")
    t = evaluate_tradable_volume_spike(
        candles, setup, min_ratio=5.0, min_price_move_pct=0.35, min_candle_range_pct=0.40
    )
    assert t is not None
    assert t.metrics.ratio >= 5.0
    assert t.metrics.direction == "up"


def test_rejects_volume_against_setup():
    candles = [_c(100, 1, 1, 1, 1) for _ in range(24)]
    candles.append(_c(600, 1.008, 1.01, 0.99, 1.0))  # down move, long setup
    setup = _Setup("long", "await_retest")
    assert evaluate_tradable_volume_spike(candles, setup, min_ratio=5.0) is None


def test_rejects_without_smc_setup():
    candles = [_c(100, 1, 1, 1, 1) for _ in range(24)]
    candles.append(_c(600, 1, 1.01, 0.99, 1.008))
    assert evaluate_tradable_volume_spike(candles, None, require_setup=True) is None
