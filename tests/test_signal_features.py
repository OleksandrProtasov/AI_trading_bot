from core.signal_features import FEATURE_NAMES, row_to_vector


def test_feature_vector_length():
    row = {name: 0.0 for name in FEATURE_NAMES}
    row["confidence"] = 0.7
    vec = row_to_vector(row)
    assert len(vec) == len(FEATURE_NAMES)
