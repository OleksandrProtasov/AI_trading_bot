from core.bearish_regime import is_bearish_regime


def test_bearish_when_emergency_and_sell_dominates():
    assert is_bearish_regime(
        buy_count=1,
        sell_count=2,
        exit_count=1,
        emergency_count=1,
        bearish_pressure=1,
        buy_score=0.5,
        sell_score=0.55,
    )


def test_not_bearish_clean_buy_context():
    assert not is_bearish_regime(
        buy_count=2,
        sell_count=0,
        exit_count=0,
        emergency_count=0,
        bearish_pressure=0,
        buy_score=0.7,
        sell_score=0.2,
    )
