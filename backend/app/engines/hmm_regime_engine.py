"""
Gaussian Hidden Markov Model (HMM) Regime Engine
Estimates smooth probabilistic regime states (Bullish Trend, Bearish Volatility, Ranging Chop)
via Baum-Welch Expectation-Maximization and Forward-Backward posterior inference.
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class HMMRegimeState:
    dominant_state: str  # "bullish_trend", "bearish_trend", "ranging_chop"
    state_probabilities: dict[str, float]
    log_likelihood: float
    volatility_state: str
    trend_state: str


class GaussianHMMRegimeEngine:
    """3-State Hidden Markov Model for market regime probability estimation."""

    STATES = ["bullish_trend", "bearish_trend", "ranging_chop"]

    def __init__(self, n_states: int = 3, n_iter: int = 20):
        if n_states != 3:
            raise ValueError("GaussianHMMRegimeEngine currently supports exactly 3 states")
        if n_iter < 1:
            raise ValueError("n_iter must be at least 1")
        self.n_states = n_states
        self.n_iter = n_iter

    def fit_predict(self, df: pd.DataFrame) -> HMMRegimeState:
        """Fit HMM on log-returns and realized volatility and infer current regime probabilities."""
        if df is None or len(df) < 30:
            return HMMRegimeState(
                dominant_state="ranging_chop",
                state_probabilities={"bullish_trend": 0.33, "bearish_trend": 0.33, "ranging_chop": 0.34},
                log_likelihood=0.0,
                volatility_state="medium",
                trend_state="neutral",
            )

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        # Observations: [Log-Returns, Normalized ATR]
        log_ret = np.diff(np.log(np.maximum(closes, 1e-6)))
        tr = np.maximum.reduce([
            highs[1:] - lows[1:],
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ])
        norm_tr = tr / np.maximum(closes[1:], 1e-6)

        X = np.column_stack([log_ret, norm_tr])
        T_steps = len(X)
        if not np.isfinite(X).all():
            raise ValueError("HMM observations contain NaN or infinite values")
        if float(np.max(np.std(X, axis=0))) < 1e-10:
            return HMMRegimeState(
                dominant_state="ranging_chop",
                state_probabilities={"bullish_trend": 0.0, "bearish_trend": 0.0, "ranging_chop": 1.0},
                log_likelihood=0.0,
                volatility_state="low",
                trend_state="neutral",
            )

        # Initialize priors, transition matrix, and Gaussian means/covariances
        pi = np.array([0.4, 0.2, 0.4])
        A = np.array([
            [0.85, 0.05, 0.10],
            [0.05, 0.80, 0.15],
            [0.10, 0.10, 0.80],
        ])
        means = np.array([
            [np.mean(log_ret) + np.std(log_ret) * 0.5, np.median(norm_tr) * 0.8],   # Bullish Low-Vol
            [np.mean(log_ret) - np.std(log_ret) * 0.8, np.median(norm_tr) * 1.6],   # Bearish High-Vol
            [0.0, np.median(norm_tr)],                                               # Chop
        ])
        variances = np.array([
            [np.var(log_ret) * 0.8 + 1e-6, np.var(norm_tr) * 0.8 + 1e-6],
            [np.var(log_ret) * 2.0 + 1e-6, np.var(norm_tr) * 2.0 + 1e-6],
            [np.var(log_ret) * 1.0 + 1e-6, np.var(norm_tr) * 1.0 + 1e-6],
        ])

        def expectation():
            """Scaled forward/backward pass with a real sequence likelihood."""
            emissions = np.zeros((T_steps, self.n_states))
            for state_idx in range(self.n_states):
                diff = X - means[state_idx]
                exponent = -0.5 * np.sum(
                    (diff ** 2) / variances[state_idx], axis=1
                )
                norm_const = 1.0 / (
                    2.0
                    * np.pi
                    * np.sqrt(variances[state_idx, 0] * variances[state_idx, 1])
                )
                emissions[:, state_idx] = np.maximum(
                    norm_const * np.exp(np.clip(exponent, -50, 0)), 1e-12
                )

            forward = np.zeros((T_steps, self.n_states))
            scales = np.ones(T_steps)
            forward[0] = pi * emissions[0]
            scales[0] = max(float(np.sum(forward[0])), 1e-300)
            forward[0] /= scales[0]
            for step in range(1, T_steps):
                forward[step] = np.dot(forward[step - 1], A) * emissions[step]
                scales[step] = max(float(np.sum(forward[step])), 1e-300)
                forward[step] /= scales[step]

            backward = np.zeros((T_steps, self.n_states))
            backward[-1] = 1.0
            for step in range(T_steps - 2, -1, -1):
                backward[step] = np.dot(
                    A, backward[step + 1] * emissions[step + 1]
                ) / scales[step + 1]

            posterior = forward * backward
            posterior /= np.maximum(
                np.sum(posterior, axis=1, keepdims=True), 1e-300
            )
            return emissions, forward, backward, posterior, scales

        # EM Iterations (Baum-Welch)
        for _ in range(self.n_iter):
            B, alpha, beta, gamma, scales = expectation()

            pi = gamma[0].copy()
            transition_counts = np.zeros_like(A)
            for step in range(T_steps - 1):
                xi = (
                    alpha[step, :, None]
                    * A
                    * (B[step + 1] * beta[step + 1])[None, :]
                )
                xi_sum = float(np.sum(xi))
                if xi_sum > 0:
                    transition_counts += xi / xi_sum
            row_sums = np.sum(transition_counts, axis=1, keepdims=True)
            A = np.divide(
                transition_counts,
                row_sums,
                out=A.copy(),
                where=row_sums > 0,
            )

            # Update means and variances
            for k in range(self.n_states):
                w = gamma[:, k]
                sum_w = np.sum(w) or 1.0
                means[k] = np.sum(w[:, None] * X, axis=0) / sum_w
                variances[k] = np.maximum(np.sum(w[:, None] * (X - means[k]) ** 2, axis=0) / sum_w, 1e-6)

        # Recompute with the final parameters and map learned states by return
        # mean so label switching cannot invert bullish and bearish outputs.
        _B, alpha, _beta, gamma, scales = expectation()
        ranked = np.argsort(means[:, 0], kind="stable")
        bear_idx = int(ranked[0])
        range_idx = int(ranked[1])
        bull_idx = int(ranked[2])
        current_probs = gamma[-1][[bull_idx, bear_idx, range_idx]]
        prob_sum = float(np.sum(current_probs))
        if not np.isfinite(current_probs).all() or prob_sum <= 0:
            current_probs = np.array([0.0, 0.0, 1.0])
        else:
            current_probs = current_probs / prob_sum
        dominant_idx = int(np.argmax(current_probs))
        dominant_label = self.STATES[dominant_idx]

        probs_dict = {
            "bullish_trend": round(float(current_probs[0]), 4),
            "bearish_trend": round(float(current_probs[1]), 4),
            "ranging_chop": round(float(current_probs[2]), 4),
        }

        # Volatility is an independent observed dimension, not bearishness.
        expected_range = float(np.dot(gamma[-1], means[:, 1]))
        low_range, high_range = np.quantile(X[:, 1], [0.25, 0.75])
        vol_state = "high" if expected_range > high_range else "low" if expected_range < low_range else "medium"
        trend_state = "bullish" if current_probs[0] > 0.45 else "bearish" if current_probs[1] > 0.45 else "neutral"

        return HMMRegimeState(
            dominant_state=dominant_label,
            state_probabilities=probs_dict,
            log_likelihood=round(float(np.sum(np.log(scales))), 2),
            volatility_state=vol_state,
            trend_state=trend_state,
        )
