"""Train signal-quality ML model from aggregated_outcomes."""
from __future__ import annotations

import argparse
import json

from core.runtime_paths import resolved_database_path
from core.signal_model import train_signal_model


def main() -> None:
    p = argparse.ArgumentParser(description="Train logistic signal-quality model")
    p.add_argument("--db-path", type=str, default="")
    p.add_argument("--min-samples", type=int, default=80)
    args = p.parse_args()

    db_path = args.db_path or resolved_database_path()
    result = train_signal_model(db_path, min_samples=int(args.min_samples))
    print(
        json.dumps(
            {
                "ok": result.ok,
                "samples": result.samples,
                "train_accuracy": result.train_accuracy,
                "test_accuracy": result.test_accuracy,
                "positive_rate": result.positive_rate,
                "model_path": result.model_path,
                "meta_path": result.meta_path,
                "note": result.note,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not result.ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
