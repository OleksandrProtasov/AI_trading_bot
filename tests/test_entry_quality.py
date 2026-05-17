from core.entry_quality import passes_entry_quality


class _Sig:
    def __init__(self, agent_type: str):
        self.agent_type = agent_type


def test_buy_requires_quality_agent():
    buy = [_Sig("shitcoin"), _Sig("emergency")]
    ok, reason = passes_entry_quality(
        "BUY",
        0.7,
        buy,
        [],
        min_unique_agents=2,
        min_directional_confidence=0.58,
    )
    assert ok is False
    assert "market/onchain/liquidity" in reason


def test_buy_passes_with_market_and_onchain():
    buy = [_Sig("market"), _Sig("onchain")]
    ok, _ = passes_entry_quality(
        "BUY",
        0.65,
        buy,
        [],
        min_unique_agents=2,
        min_directional_confidence=0.58,
    )
    assert ok is True
