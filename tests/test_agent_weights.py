from core.agent_weights import derive_weights_from_edge


def test_derive_weights_downweights_unknown():
    weights = derive_weights_from_edge(
        {
            "by_agent": [
                {
                    "agent_type": "unknown",
                    "avg_return_pct": -0.2,
                    "hit_rate": 0.2,
                },
                {
                    "agent_type": "market",
                    "avg_return_pct": 0.02,
                    "hit_rate": 0.42,
                },
            ]
        }
    )
    assert weights["unknown"] < weights["market"]
