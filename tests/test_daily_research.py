"""Daily research promotion and env merge helpers."""
from pathlib import Path

from core.research_artifacts import (
    merge_env_file,
    params_to_env_lines,
    parse_env_file,
    should_promote,
)


def test_should_promote_blocks_high_drawdown():
    current = {"score": 5.0, "avg_drawdown_pct": 15.0}
    previous = {"score": 1.0, "avg_drawdown_pct": 5.0}
    assert (
        should_promote(
            current=current,
            previous=previous,
            min_score_delta=0.1,
            max_drawdown_pct=12.0,
            promote_on_equal=False,
        )
        is False
    )


def test_should_promote_requires_delta_by_default():
    current = {"score": 1.0, "avg_drawdown_pct": 8.0}
    previous = {"score": 1.0, "avg_drawdown_pct": 8.0}
    assert (
        should_promote(
            current=current,
            previous=previous,
            min_score_delta=0.2,
            max_drawdown_pct=12.0,
            promote_on_equal=False,
        )
        is False
    )


def test_should_promote_on_equal():
    current = {"score": 1.0, "avg_drawdown_pct": 8.0}
    previous = {"score": 1.0, "avg_drawdown_pct": 8.0}
    assert (
        should_promote(
            current=current,
            previous=previous,
            min_score_delta=0.2,
            max_drawdown_pct=12.0,
            promote_on_equal=True,
        )
        is True
    )


def test_merge_env_file_updates_and_appends(tmp_path: Path):
    target = tmp_path / ".env"
    target.write_text("FOO=1\nAGG_EV_BUFFER_BPS=5\n", encoding="utf-8")
    updated, appended = merge_env_file(
        target,
        {
            "AGG_EV_BUFFER_BPS": "8",
            "AGG_EV_CONFIDENCE_MULT": "18",
            "TELEGRAM_TOKEN": "must-not-touch",
        },
    )
    assert updated == ["AGG_EV_BUFFER_BPS"]
    assert appended == ["AGG_EV_CONFIDENCE_MULT"]
    text = target.read_text(encoding="utf-8")
    assert "AGG_EV_BUFFER_BPS=8" in text
    assert "AGG_EV_CONFIDENCE_MULT=18" in text
    assert "TELEGRAM_TOKEN" not in text


def test_params_to_env_lines():
    lines = params_to_env_lines({"ev_buffer_bps": 8, "ev_margin_mult": 22})
    joined = "\n".join(lines)
    assert "AGG_EV_BUFFER_BPS=8" in joined
    assert "AGG_EV_MARGIN_MULT=22" in joined


def test_parse_env_file_ignores_comments(tmp_path: Path):
    p = tmp_path / "x.env"
    p.write_text("# comment\nAGG_EV_BUFFER_BPS=9\n", encoding="utf-8")
    assert parse_env_file(p)["AGG_EV_BUFFER_BPS"] == "9"
