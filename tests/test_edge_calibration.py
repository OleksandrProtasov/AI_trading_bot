from core.edge_calibration import (
    _combined_extra_bps,
    _extra_bps_from_avg_return,
    _extra_bps_from_hit_rate,
    bucket_key,
    calibration_extra_bps,
)


def test_extra_bps_penalizes_negative_avg():
    assert _extra_bps_from_avg_return(-0.2) > 10.0


def test_hit_rate_penalty_when_below_target():
    hit_part = _extra_bps_from_hit_rate(0.40, 0.02, min_hit_rate=0.45, weak_return_pct=0.05)
    assert hit_part > 3.0


def test_combined_penalty_caps_at_max():
    total, ret_p, hit_p = _combined_extra_bps(
        0.01,
        0.40,
        net_target_pct=0.08,
        min_hit_rate=0.45,
        weak_return_pct=0.05,
        max_extra_bps=30.0,
    )
    assert total <= 30.0
    assert ret_p > 0
    assert hit_p > 0


def test_calibration_extra_bps_lookup():
    cal = {
        "buckets": {
            "BUY:0.80-1.00": {
                "extra_required_bps": 12.0,
                "return_penalty_bps": 5.0,
                "hit_rate_penalty_bps": 7.0,
                "hit_rate": 0.40,
                "avg_return_pct": -0.05,
                "n": 100,
            }
        }
    }
    extra, note = calibration_extra_bps(
        cal, action="BUY", confidence=0.85, enabled=True
    )
    assert extra == 12.0
    assert "BUY:0.80-1.00" in note


def test_bucket_key():
    assert bucket_key("buy", 0.85) == "BUY:0.80-1.00"
