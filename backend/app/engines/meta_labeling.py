"""
Meta-Labeling & Triple-Barrier Engine (Marcos López de Prado Framework)
Decouples trade direction (Primary SMC/CVD/SQZ Model) from trade sizing & execution decision
(Secondary Meta-Model Classifier). Prevents temporal leakage via Purged K-Fold CV.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import numpy as np
import pandas as pd


@dataclass
class TripleBarrierLabel:
    entry_index: int
    entry_time: Any
    exit_index: int
    exit_time: Any
    direction: str
    entry_price: float
    exit_price: float
    ret: float
    target_r: float
    barrier_hit: str  # "upper", "lower", "vertical"
    label: int        # 1 = profitable touch (Upper hit first), 0 = loss/timeout


@dataclass
class MetaModelEvaluation:
    train_samples: int
    test_samples: int
    cv_folds: int
    raw_win_rate_pct: float       # Win rate of primary model before meta-filtering
    filtered_win_rate_pct: float  # Win rate after meta-model filtering
    precision_gain_pct: float     # Improvement in precision
    retained_trades_pct: float    # % of trades approved by meta-model
    feature_importances: dict[str, float] = field(default_factory=dict)


class MetaLabelingEngine:
    """Implements López de Prado Triple-Barrier labeling and secondary meta-classification."""

    @staticmethod
    def apply_triple_barrier(
        df: pd.DataFrame,
        events: list[dict[str, Any]],
        *,
        pt_atr_mult: float = 2.0,
        sl_atr_mult: float = 1.0,
        holding_bars: int = 24,
    ) -> list[TripleBarrierLabel]:
        """
        Label each primary signal using horizontal profit/loss barriers and a vertical time barrier.
        """
        labels: list[TripleBarrierLabel] = []
        if df is None or df.empty or not events:
            return labels

        highs = df["high"].values
        lows = df["low"].values
        closes = df["close"].values
        n_bars = len(df)

        for event in events:
            idx = int(event.get("index", 0))
            if idx >= n_bars - 1:
                continue

            entry_p = float(event.get("entry_price", closes[idx]))
            direction = str(event.get("direction", "long")).lower()
            atr = float(event.get("atr", entry_p * 0.01))
            atr = max(atr, entry_p * 0.002)

            pt_price = entry_p + atr * pt_atr_mult if direction == "long" else entry_p - atr * pt_atr_mult
            sl_price = entry_p - atr * sl_atr_mult if direction == "long" else entry_p + atr * sl_atr_mult
            end_idx = min(n_bars - 1, idx + holding_bars)

            barrier_hit = "vertical"
            exit_idx = end_idx
            exit_p = float(closes[end_idx])

            for step in range(idx + 1, end_idx + 1):
                h = highs[step]
                l = lows[step]
                if direction == "long":
                    # OHLC bars do not reveal which barrier was touched first;
                    # use conservative stop-first ordering when both are hit.
                    if l <= sl_price:
                        barrier_hit = "lower"
                        exit_idx = step
                        exit_p = sl_price
                        break
                    elif h >= pt_price:
                        barrier_hit = "upper"
                        exit_idx = step
                        exit_p = pt_price
                        break
                else:  # short
                    if h >= sl_price:
                        barrier_hit = "lower"
                        exit_idx = step
                        exit_p = sl_price
                        break
                    elif l <= pt_price:
                        barrier_hit = "upper"
                        exit_idx = step
                        exit_p = pt_price
                        break

            ret = (exit_p - entry_p) / entry_p if direction == "long" else (entry_p - exit_p) / entry_p
            label_val = 1 if barrier_hit == "upper" else 0

            labels.append(
                TripleBarrierLabel(
                    entry_index=idx,
                    entry_time=df.index[idx] if hasattr(df.index, "__getitem__") else idx,
                    exit_index=exit_idx,
                    exit_time=df.index[exit_idx] if hasattr(df.index, "__getitem__") else exit_idx,
                    direction=direction,
                    entry_price=round(entry_p, 4),
                    exit_price=round(exit_p, 4),
                    ret=round(ret, 6),
                    target_r=pt_atr_mult / sl_atr_mult,
                    barrier_hit=barrier_hit,
                    label=label_val,
                )
            )

        return labels

    @staticmethod
    def extract_features(events: list[dict[str, Any]]) -> np.ndarray:
        """Extract quantitative feature vectors for meta-classifier training."""
        features = []
        for e in events:
            features.append([
                float(e.get("confluence_score", 50.0)) / 100.0,
                float(e.get("smc_score", 20.0)) / 40.0,
                float(e.get("cvd_score", 15.0)) / 30.0,
                float(e.get("sqz_score", 15.0)) / 30.0,
                float(e.get("body_ratio", 0.50)),
                min(float(e.get("vol_ratio", 1.0)), 5.0) / 5.0,
                float(e.get("path_efficiency", 0.50)),
                min(float(e.get("atr_pct", 1.0)), 5.0) / 5.0,
                float(e.get("delta_ratio", 0.0)),
                (float(e.get("hour", 12.0))) / 24.0,
            ])
        return np.array(features, dtype=float)

    @staticmethod
    def purged_kfold_cv(
        n_samples: int,
        labels: list[TripleBarrierLabel],
        n_splits: int = 5,
        embargo_pct: float = 0.01,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """
        Generate purged & embargoed training/testing index splits.
        Prevents lookahead leakage from overlapping holding periods.
        """
        if n_samples != len(labels):
            raise ValueError("n_samples must match labels length")
        if n_samples < 2:
            raise ValueError("At least two samples are required")
        if not 2 <= n_splits <= n_samples:
            raise ValueError("n_splits must be between 2 and n_samples")
        if not 0.0 <= embargo_pct < 1.0:
            raise ValueError("embargo_pct must be in [0, 1)")
        indices = np.arange(n_samples)
        fold_size = n_samples // n_splits
        embargo = int(n_samples * embargo_pct)
        splits = []

        for i in range(n_splits):
            test_start = i * fold_size
            test_end = (i + 1) * fold_size if i < n_splits - 1 else n_samples
            test_idx = indices[test_start:test_end]

            # Find test span time boundaries
            test_min_entry = min(labels[j].entry_index for j in test_idx)
            test_max_exit = max(labels[j].exit_index for j in test_idx) + embargo

            # Purge training indices that overlap with test holding periods
            train_idx = [
                j for j in indices
                if j not in test_idx
                and not (labels[j].entry_index <= test_max_exit and labels[j].exit_index >= test_min_entry)
            ]
            splits.append((np.array(train_idx, dtype=int), np.array(test_idx, dtype=int)))

        return splits

    def evaluate_meta_model(
        self,
        events: list[dict[str, Any]],
        labels: list[TripleBarrierLabel],
        *,
        threshold: float = 0.55,
        n_splits: int = 5,
    ) -> MetaModelEvaluation:
        """
        Train a calibrated Logistic/Ensemble meta-classifier with Purged K-Fold CV.
        """
        if len(events) != len(labels):
            raise ValueError("events and labels must contain the same number of samples")
        if len(events) < 20:
            return MetaModelEvaluation(
                train_samples=len(events),
                test_samples=len(labels),
                cv_folds=1,
                raw_win_rate_pct=50.0,
                filtered_win_rate_pct=50.0,
                precision_gain_pct=0.0,
                retained_trades_pct=100.0,
            )
        X = self.extract_features(events)
        y = np.array([lbl.label for lbl in labels], dtype=int)
        n_samples = len(y)

        splits = self.purged_kfold_cv(n_samples, labels, n_splits=n_splits)

        raw_wins = int(np.sum(y == 1))
        raw_win_rate = (raw_wins / n_samples) * 100.0

        filtered_outcomes = []
        weights = np.zeros(X.shape[1])
        evaluated_train: set[int] = set()
        evaluated_test: set[int] = set()
        completed_folds = 0

        # Purged CV loop with Ridge Logistic Classifier
        for train_idx, test_idx in splits:
            if len(train_idx) < 10 or len(test_idx) < 2:
                continue
            completed_folds += 1
            evaluated_train.update(int(i) for i in train_idx)
            evaluated_test.update(int(i) for i in test_idx)

            X_tr, y_tr = X[train_idx], y[train_idx]
            X_te, y_te = X[test_idx], y[test_idx]

            # Fit Regularized Logistic Regression (closed-form IRLS / gradient)
            w = np.zeros(X_tr.shape[1])
            bias = 0.0
            lr = 0.1
            l2 = 0.01

            for _ in range(50):
                z = np.dot(X_tr, w) + bias
                p = 1.0 / (1.0 + np.exp(-np.clip(z, -10, 10)))
                grad_w = np.dot(X_tr.T, (p - y_tr)) / len(y_tr) + l2 * w
                grad_b = np.mean(p - y_tr)
                w -= lr * grad_w
                bias -= lr * grad_b

            weights += np.abs(w)

            # Predict test set probabilities
            z_te = np.dot(X_te, w) + bias
            probs = 1.0 / (1.0 + np.exp(-np.clip(z_te, -10, 10)))

            for prob, true_y in zip(probs, y_te):
                if prob >= threshold:
                    filtered_outcomes.append(true_y)

        if filtered_outcomes:
            filt_wins = sum(filtered_outcomes)
            filt_win_rate = (filt_wins / len(filtered_outcomes)) * 100.0
            retained_pct = (len(filtered_outcomes) / n_samples) * 100.0
        else:
            filt_win_rate = 0.0
            retained_pct = 0.0

        feat_names = [
            "Confluence", "SMC Score", "CVD Score", "SQZ Score",
            "Candle Body", "Volume Ratio", "Path Efficiency",
            "ATR %", "Delta Ratio", "Hour of Day",
        ]
        norm_w = weights / (np.sum(weights) or 1.0)
        importances = {name: round(float(w), 4) for name, w in zip(feat_names, norm_w)}

        return MetaModelEvaluation(
            train_samples=len(evaluated_train),
            test_samples=len(evaluated_test),
            cv_folds=completed_folds,
            raw_win_rate_pct=round(raw_win_rate, 2),
            filtered_win_rate_pct=round(filt_win_rate, 2),
            precision_gain_pct=round(filt_win_rate - raw_win_rate, 2),
            retained_trades_pct=round(retained_pct, 2),
            feature_importances=importances,
        )
