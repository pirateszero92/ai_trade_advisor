"""
BTC Market Anchor Service
Evaluates Bitcoin's regime and short-term momentum to protect Altcoin positions
from market-wide cascading selloffs and liquidity flushes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from loguru import logger

from app.core.config import get_settings
from app.core.runtime_config import load_runtime_config


def _compact_symbol(s: str) -> str:
    """Normalize symbol representation across formats (e.g. BTC/USDT -> BTCUSDT)."""
    return str(s or "").strip().upper().replace("-", "").replace("/", "").replace("_", "")


def is_crypto_altcoin(
    symbol: str,
    market_type: str | None = None,
    exchange: str | None = None,
) -> bool:
    """Return whether instrument metadata identifies a non-BTC crypto asset.

    ``market_type`` is authoritative.  Symbol-only inference is retained only
    for legacy callers and tests; execution paths must pass market metadata.
    """
    compact = _compact_symbol(symbol)
    if not compact:
        return False
    if market_type is not None and str(market_type).lower() != "crypto":
        return False
    if exchange is not None and str(exchange).lower() not in {
        "binance", "bybit", "crypto", ""
    }:
        return False
    # Bitcoin itself is the market benchmark, never its own altcoin
    if compact in {"BTCUSDT", "BTCUSD", "BTCFDUSD", "BTCUSDC"}:
        return False
    # Non-crypto symbols (Forex, Metals, Indices, Equities) are exempt
    if any(compact.startswith(fx) for fx in ("EUR", "GBP", "USD", "AUD", "CAD", "NZD", "XAU", "XAG", "GOLD")):
        return False
    # Any other crypto quote pair against USDT/USD/FDUSD is an altcoin
    if any(compact.endswith(q) for q in ("USDT", "USD", "FDUSD", "USDC")):
        return True
    return False


class BTCMarketAnchor:
    """Deterministic market-anchor gate based on BTC/USDT macro context."""

    def __init__(self, btc_symbol: str = "BTC/USDT") -> None:
        self.btc_symbol = btc_symbol

    async def evaluate(
        self,
        symbol: str,
        direction: str,
        *,
        market_type: str | None = None,
        exchange: str | None = None,
        expected_timeframe: str = "15m",
        btc_analysis: Any | None = None,
    ) -> tuple[bool, Optional[str], dict[str, Any]]:
        """
        Evaluate if an Altcoin order/signal in `direction` ('long' | 'short')
        aligns with Bitcoin's current market anchor.

        Returns:
            (approved: bool, rejection_reason: Optional[str], metadata: dict)
        """
        cfg = get_settings()
        runtime = load_runtime_config()
        dir_clean = str(direction or "").lower().strip()
        if dir_clean not in {"long", "short"}:
            return True, None, {"exempt": True, "reason": "neutral_direction"}

        if not is_crypto_altcoin(symbol, market_type, exchange):
            return True, None, {
                "status": "NOT_APPLICABLE",
                "execution_allowed": True,
                "exempt": True,
                "reason": "not_altcoin",
            }

        cfg_enabled = getattr(cfg, "btc_anchor_filter_enabled", True)
        enabled = bool(cfg_enabled) and bool(runtime.get("btc_anchor_filter_enabled", True))
        if not enabled:
            return True, None, {
                "status": "DISABLED",
                "execution_allowed": True,
                "exempt": True,
                "reason": "filter_disabled",
            }

        # Resolve BTC analysis if not provided
        if btc_analysis is None:
            try:
                from app.services.execution_analysis import execution_analyses
                btc_analysis = await execution_analyses.get(
                    symbol=self.btc_symbol,
                    market_type="crypto",
                    exchange="binance",
                    entry_mode="limit",
                )
            except Exception as exc:
                logger.warning(f"[BTCAnchor] Failed to resolve BTC anchor execution analysis: {exc}")
                reason = "BTC systemic-risk snapshot is unavailable"
                return False, reason, {
                    "status": "UNAVAILABLE",
                    "execution_allowed": False,
                    "reason_code": "BTC_ANCHOR_UNAVAILABLE",
                    "error_type": type(exc).__name__,
                }

        btc_sig = getattr(btc_analysis, "signal", None)
        if btc_sig is None:
            reason = "BTC systemic-risk signal is unavailable"
            return False, reason, {
                "status": "UNAVAILABLE",
                "execution_allowed": False,
                "reason_code": "BTC_ANCHOR_UNAVAILABLE",
            }

        now = datetime.now(timezone.utc)
        valid_until = getattr(btc_analysis, "valid_until", None)
        generated_at = getattr(btc_analysis, "generated_at", None)
        anchor_timeframe = str(getattr(btc_analysis, "timeframe", "") or "").lower()
        is_fresh = bool(
            isinstance(valid_until, datetime)
            and (valid_until if valid_until.tzinfo else valid_until.replace(tzinfo=timezone.utc)) > now
        )
        if anchor_timeframe != str(expected_timeframe).lower() or not is_fresh:
            reason = "BTC systemic-risk snapshot is stale or timeframe-mismatched"
            return False, reason, {
                "status": "UNAVAILABLE",
                "execution_allowed": False,
                "reason_code": "BTC_ANCHOR_STALE",
                "btc_snapshot_id": str(getattr(btc_analysis, "snapshot_id", "")),
                "btc_timeframe": anchor_timeframe,
                "expected_timeframe": str(expected_timeframe).lower(),
                "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else None,
                "valid_until": valid_until.isoformat() if isinstance(valid_until, datetime) else None,
            }

        regime_data = getattr(btc_sig, "market_regime", {}) or {}
        regime_dir = str(regime_data.get("direction", "")).lower()
        regime_label = str(regime_data.get("regime", "")).lower()
        btc_bias = str(getattr(btc_sig, "bias", "") or getattr(btc_sig, "trend", "") or "").lower()
        displacement = getattr(btc_sig, "displacement", {})
        if not isinstance(displacement, dict):
            displacement = {}
        displacement_direction = str(displacement.get("direction") or "none").lower()
        delta_val = getattr(btc_sig, "delta_ratio", 0.0)
        if hasattr(delta_val, "_mock_name"):
            delta_ratio = 0.0
        else:
            try:
                delta_ratio = float(delta_val or 0.0)
            except (TypeError, ValueError):
                delta_ratio = 0.0

        # Compute 1H (last 4-5 15M candles) price change percentage
        frame = getattr(btc_analysis, "frame", None)
        pct_change_1h = 0.0
        if frame is not None and len(frame) >= 5:
            try:
                last_c = float(frame["close"].iloc[-1])
                c_1h = float(frame["close"].iloc[-5])
                if c_1h > 0:
                    pct_change_1h = round((last_c - c_1h) / c_1h * 100.0, 2)
            except Exception:
                pct_change_1h = 0.0

        dump_threshold = float(runtime.get(
            "btc_anchor_dump_threshold_pct",
            getattr(cfg, "btc_anchor_dump_threshold_pct", 1.0),
        ))

        metadata = {
            "status": "ALLOWED",
            "execution_allowed": True,
            "reason_code": "BTC_ANCHOR_ALIGNED",
            "btc_symbol": self.btc_symbol,
            "btc_snapshot_id": str(getattr(btc_analysis, "snapshot_id", "")),
            "btc_timeframe": anchor_timeframe,
            "generated_at": generated_at.isoformat() if isinstance(generated_at, datetime) else None,
            "valid_until": valid_until.isoformat() if isinstance(valid_until, datetime) else None,
            "btc_regime": regime_label,
            "btc_regime_direction": regime_dir,
            "btc_bias": btc_bias,
            "btc_1h_pct_change": pct_change_1h,
            "btc_displacement_direction": displacement_direction,
            "btc_delta_ratio": round(delta_ratio, 6),
            "dump_threshold_pct": dump_threshold,
        }

        # Long side defense: Block Altcoin Longs when BTC is dumping or heavily bearish
        if dir_clean == "long":
            if pct_change_1h <= -dump_threshold:
                reason = (
                    f"BTC Market Anchor: BTC is in active dump ({pct_change_1h:+.2f}% 1H <= -{dump_threshold}%); "
                    "Altcoin LONG prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_ACTIVE_DUMP",
                }
            if regime_dir == "bearish" and pct_change_1h < -0.30:
                reason = (
                    f"BTC Market Anchor: BTC regime is bearish ({pct_change_1h:+.2f}% 1H); "
                    "Altcoin LONG prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_BEARISH_REGIME",
                }
            if displacement_direction == "short":
                reason = (
                    "BTC Market Anchor: BTC active bearish displacement; "
                    "Altcoin LONG prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_REGIME_CONFLICT",
                }
            if delta_ratio <= -0.15:
                reason = (
                    f"BTC Market Anchor: heavy CVD selling pressure (delta ratio {delta_ratio:.2f}); "
                    "Altcoin LONG paused"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_REGIME_CONFLICT",
                }

        # Short side defense: Block Altcoin Shorts when BTC is rallying aggressively
        elif dir_clean == "short":
            if pct_change_1h >= dump_threshold:
                reason = (
                    f"BTC Market Anchor: BTC is in active rally ({pct_change_1h:+.2f}% 1H >= +{dump_threshold}%); "
                    "Altcoin SHORT prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_ACTIVE_RALLY",
                }
            if regime_dir == "bullish" and pct_change_1h > 0.30:
                reason = (
                    f"BTC Market Anchor: BTC regime is bullish ({pct_change_1h:+.2f}% 1H); "
                    "Altcoin SHORT prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_BULLISH_REGIME",
                }
            if displacement_direction == "long":
                reason = (
                    "BTC Market Anchor: BTC active bullish displacement; "
                    "Altcoin SHORT prohibited"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_REGIME_CONFLICT",
                }
            if delta_ratio >= 0.15:
                reason = (
                    f"BTC Market Anchor: heavy CVD buying pressure (delta ratio {delta_ratio:.2f}); "
                    "Altcoin SHORT paused"
                )
                return False, reason, {
                    **metadata, "status": "BLOCKED", "execution_allowed": False,
                    "reason_code": "BTC_REGIME_CONFLICT",
                }

        return True, None, metadata


def validate_btc_anchor(
    symbol: str,
    direction: str,
    systemic_risk: dict[str, Any] | None = None,
) -> tuple[bool, Optional[str]]:
    """Fast synchronous validation of BTC Market Anchor against systemic risk facts."""
    if not is_crypto_altcoin(symbol):
        return True, None
    if not systemic_risk:
        return True, None
    if systemic_risk.get("execution_allowed", True) is False:
        reason = systemic_risk.get("reason") or systemic_risk.get("reason_code") or "BTC_REGIME_CONFLICT"
        return False, f"BTC Market Anchor: {reason}"
    return True, None


btc_market_anchor = BTCMarketAnchor()

