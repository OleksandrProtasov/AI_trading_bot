"""Trainable signal-quality model (sklearn) + runtime gate."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.runtime_paths import repo_root
from core.signal_features import FEATURE_NAMES, build_feature_row, row_to_vector

DEFAULT_MODEL_PATH = repo_root() / "reports" / "signal_model.joblib"
DEFAULT_META_PATH = repo_root() / "reports" / "signal_model_meta.json"


@dataclass
class ModelTrainResult:
    ok: bool
    samples: int
    train_accuracy: float
    test_accuracy: float
    positive_rate: float
    model_path: str
    meta_path: str
    note: str = ""


def train_signal_model(
    db_path: str,
    *,
    min_samples: int = 80,
    model_path: Path | None = None,
    meta_path: Path | None = None,
) -> ModelTrainResult:
    from core.signal_features import load_training_dataset

    model_path = model_path or DEFAULT_MODEL_PATH
    meta_path = meta_path or DEFAULT_META_PATH

    x_rows, y_rows, meta = load_training_dataset(db_path, min_samples=min_samples)
    n = len(x_rows)
    if n < min_samples:
        return ModelTrainResult(
            ok=False,
            samples=n,
            train_accuracy=0.0,
            test_accuracy=0.0,
            positive_rate=float(meta.get("positive_rate") or 0.0),
            model_path=str(model_path),
            meta_path=str(meta_path),
            note=f"Need at least {min_samples} samples, got {n}.",
        )

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import accuracy_score
        from sklearn.model_selection import train_test_split
        import joblib
    except ImportError as exc:
        return ModelTrainResult(
            ok=False,
            samples=n,
            train_accuracy=0.0,
            test_accuracy=0.0,
            positive_rate=float(meta.get("positive_rate") or 0.0),
            model_path=str(model_path),
            meta_path=str(meta_path),
            note=f"scikit-learn required: {exc}",
        )

    split_idx = max(1, int(n * 0.8))
    x_train, x_test = x_rows[:split_idx], x_rows[split_idx:]
    y_train, y_test = y_rows[:split_idx], y_rows[split_idx:]
    if len(x_test) < 10:
        x_train, x_test, y_train, y_test = train_test_split(
            x_rows, y_rows, test_size=0.2, random_state=42, stratify=y_rows
        )

    model = LogisticRegression(max_iter=400, class_weight="balanced")
    model.fit(x_train, y_train)
    train_acc = float(accuracy_score(y_train, model.predict(x_train)))
    test_acc = float(accuracy_score(y_test, model.predict(x_test)))

    model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "samples": n,
        "positive_rate": float(meta.get("positive_rate") or 0.0),
        "train_accuracy": train_acc,
        "test_accuracy": test_acc,
        "feature_names": FEATURE_NAMES,
        "model_type": "logistic_regression",
    }
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return ModelTrainResult(
        ok=True,
        samples=n,
        train_accuracy=train_acc,
        test_accuracy=test_acc,
        positive_rate=float(meta.get("positive_rate") or 0.0),
        model_path=str(model_path),
        meta_path=str(meta_path),
        note="Model trained and saved.",
    )


class SignalModelGate:
    """Runtime win-probability gate loaded from reports/signal_model.joblib."""

    def __init__(self, model_path: Path | None = None, meta_path: Path | None = None) -> None:
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.meta_path = meta_path or DEFAULT_META_PATH
        self.model: Any = None
        self.meta: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if not self.model_path.exists():
            return
        try:
            import joblib

            self.model = joblib.load(self.model_path)
        except Exception:
            self.model = None
            return
        if self.meta_path.exists():
            try:
                with self.meta_path.open("r", encoding="utf-8") as f:
                    self.meta = json.load(f)
            except Exception:
                self.meta = {}

    @property
    def ready(self) -> bool:
        return self.model is not None

    def predict_win_prob(self, features: Dict[str, float]) -> float:
        if not self.ready:
            return 0.5
        vec = [row_to_vector(features)]
        try:
            proba = self.model.predict_proba(vec)[0]
            # class 1 = profitable
            classes = list(getattr(self.model, "classes_", [0, 1]))
            if 1 in classes:
                idx = classes.index(1)
                return float(proba[idx])
            return float(max(proba))
        except Exception:
            return 0.5

    def evaluate(
        self,
        *,
        db_path: str,
        signal_ts: int,
        symbol: str,
        action: str,
        confidence: float,
        risk: str,
        reasons: Optional[List[str]] = None,
        council_enabled: bool = True,
        council_changed: bool = False,
        source_signals_count: int = 0,
        margin_est: float = 0.0,
        unique_agents_est: int = 0,
        min_win_prob: float = 0.42,
        enabled: bool = True,
    ) -> Tuple[bool, float, str]:
        if not enabled or not self.ready:
            return True, 0.5, ""
        if (action or "").upper() not in ("BUY", "SELL"):
            return True, 0.5, ""

        feat = build_feature_row(
            db_path=db_path,
            signal_ts=int(signal_ts),
            symbol=symbol,
            action=action,
            confidence=float(confidence),
            risk=risk,
            reasons=reasons,
            council_enabled=council_enabled,
            council_changed=council_changed,
            source_signals_count=source_signals_count,
            margin_est=margin_est,
            unique_agents_est=unique_agents_est,
        )
        p = self.predict_win_prob(feat)
        if p < float(min_win_prob):
            return (
                False,
                p,
                f"ML gate: win_prob {p:.2f} < {min_win_prob:.2f}.",
            )
        return True, p, ""
