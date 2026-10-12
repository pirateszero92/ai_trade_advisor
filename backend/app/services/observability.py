"""Lightweight Prometheus-compatible observability registry."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import Any


_DEFAULT_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


def risk_rejection_category(reason: Any) -> str:
    text = str(reason or "").lower()
    if "daily" in text and "loss" in text:
        return "daily_loss"
    if "drawdown" in text:
        return "drawdown"
    if "open position" in text or "max position" in text:
        return "max_positions"
    if "risk/reward" in text or "executable r" in text:
        return "executable_rr"
    if "circuit" in text or "halt" in text or "stop_losses" in text:
        return "circuit_breaker"
    if "notional" in text or "leverage" in text:
        return "portfolio_leverage"
    if "execution cost" in text or "cost" in text and "risk" in text:
        return "execution_cost"
    if "cluster" in text or "concentration" in text or "correlation" in text:
        return "concentration"
    if "stop" in text and "distance" in text:
        return "stop_distance"
    if "balance" in text or "equity" in text:
        return "account_equity"
    return "other"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels_text(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in labels)
    return "{" + body + "}"


class ObservabilityRegistry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]],
            dict[str, Any],
        ] = {}
    @staticmethod
    def _labels(labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((str(key), str(value)) for key, value in labels.items()))

    def inc(self, name: str, value: float = 1.0, **labels: Any) -> None:
        if value < 0 or not math.isfinite(value):
            return
        key = (name, self._labels(labels))
        with self._lock:
            self._counters[key] += float(value)

    def set_gauge(self, name: str, value: float, **labels: Any) -> None:
        if not math.isfinite(float(value)):
            return
        key = (name, self._labels(labels))
        with self._lock:
            self._gauges[key] = float(value)

    def observe(
        self,
        name: str,
        value: float,
        *,
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
        **labels: Any,
    ) -> None:
        value = float(value)
        if value < 0 or not math.isfinite(value):
            return
        key = (name, self._labels(labels))
        with self._lock:
            state = self._histograms.setdefault(
                key,
                {
                    "buckets": tuple(sorted(float(item) for item in buckets)),
                    "counts": defaultdict(int),
                    "count": 0,
                    "sum": 0.0,
                },
            )
            state["count"] += 1
            state["sum"] += value
            for bucket in state["buckets"]:
                if value <= bucket:
                    state["counts"][bucket] += 1
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {
                    self._snapshot_key(name, labels): value
                    for (name, labels), value in self._counters.items()
                },
                "gauges": {
                    self._snapshot_key(name, labels): value
                    for (name, labels), value in self._gauges.items()
                },
                "histograms": {
                    self._snapshot_key(name, labels): {
                        "count": state["count"],
                        "sum": state["sum"],
                        "buckets": dict(state["counts"]),
                    }
                    for (name, labels), state in self._histograms.items()
                },
            }

    @staticmethod
    def _snapshot_key(name: str, labels: tuple[tuple[str, str], ...]) -> str:
        return name + _labels_text(labels)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"{name}_total{_labels_text(labels)} {value:g}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"{name}{_labels_text(labels)} {value:g}")
            for (name, labels), state in sorted(self._histograms.items()):
                cumulative_labels = dict(labels)
                for bucket in state["buckets"]:
                    bucket_labels = self._labels({**cumulative_labels, "le": f"{bucket:g}"})
                    lines.append(
                        f"{name}_bucket{_labels_text(bucket_labels)} "
                        f"{state['counts'].get(bucket, 0)}"
                    )
                inf_labels = self._labels({**cumulative_labels, "le": "+Inf"})
                lines.append(
                    f"{name}_bucket{_labels_text(inf_labels)} {state['count']}"
                )
                lines.append(f"{name}_count{_labels_text(labels)} {state['count']}")
                lines.append(f"{name}_sum{_labels_text(labels)} {state['sum']:g}")
        return "\n".join(lines) + ("\n" if lines else "")

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


observability = ObservabilityRegistry()
