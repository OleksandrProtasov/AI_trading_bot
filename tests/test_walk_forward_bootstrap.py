import argparse

from walk_forward_replay import _bootstrap_profiles


def _args(**overrides):
    base = dict(
        recent_window_sec=120,
        min_score=0.35,
        min_margin=0.12,
        dedup_sec=40,
        min_confidence=0.58,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_bootstrap_never_lowers_min_confidence():
    profiles = _bootstrap_profiles(_args())
    for p in profiles:
        assert p["min_confidence"] == 0.58


def test_bootstrap_loosens_score_margin_not_below_floors():
    profiles = _bootstrap_profiles(_args())
    assert profiles[0]["min_score"] == 0.35
    assert profiles[-1]["min_score"] >= 0.35 - 0.12
    assert profiles[-1]["min_margin"] >= 0.12 - 0.08
