"""
Unit tests for BTCMarketAnchor service (Step 1 of Market Defense Upgrade Plan).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pandas as pd
import pytest

from app.core.config import get_settings
from app.services.btc_market_anchor import BTCMarketAnchor, is_crypto_altcoin


def test_is_crypto_altcoin_classification():
    # BTC benchmarks - exempt
    assert not is_crypto_altcoin("BTC/USDT")
    assert not is_crypto_altcoin("BTCUSDT")
    assert not is_crypto_altcoin("BTC-USDT")
    assert not is_crypto_altcoin("BTCUSD")
    assert not is_crypto_altcoin("BTCUSDC")

    # Altcoins - subject to anchor
    assert is_crypto_altcoin("NEAR/USDT")
    assert is_crypto_altcoin("SUI/USDT")
    assert is_crypto_altcoin("DOT/USDT")
    assert is_crypto_altcoin("ETH/USDT")
    assert is_crypto_altcoin("SOL/USDT")
    assert is_crypto_altcoin("DOGE/USDT")
    assert is_crypto_altcoin("ADA/USDT")

    # Non-crypto assets - exempt
    assert not is_crypto_altcoin("XAUUSD")
    assert not is_crypto_altcoin("XAU/USD")
    assert not is_crypto_altcoin("EURUSD")
    assert not is_crypto_altcoin("GBPUSD")
    assert not is_crypto_altcoin("AAPL")
    assert not is_crypto_altcoin("TSLA")


def _make_mock_btc_analysis(*, pct_change_1h: float, regime_direction: str = "neutral", regime_label: str = "ranging"):
    base_price = 85000.0
    end_price = base_price * (1.0 + pct_change_1h / 100.0)
    
    # 5 bars (15M each)
    prices = [base_price] * 4 + [end_price]
    frame = pd.DataFrame({
        "close": prices,
        "high": [p * 1.001 for p in prices],
        "low": [p * 0.999 for p in prices],
    })

    sig = MagicMock()
    sig.market_regime = {
        "regime": regime_label,
        "direction": regime_direction,
        "confidence": 75,
    }
    sig.bias = regime_direction
    sig.trend = regime_direction
    sig.displacement = {"direction": "none"}
    sig.delta_ratio = 0.0

    analysis = MagicMock()
    analysis.frame = frame
    analysis.signal = sig
    analysis.snapshot_id = "btc-snapshot-test"
    analysis.timeframe = "15m"
    analysis.generated_at = datetime.now(timezone.utc)
    analysis.valid_until = analysis.generated_at + timedelta(minutes=15)
    return analysis


@pytest.mark.asyncio
async def test_btc_dump_blocks_altcoin_long():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-2.10, regime_direction="bearish")

    approved, reason, meta = await anchor.evaluate("NEAR/USDT", "long", btc_analysis=mock_btc)
    assert not approved
    assert "Altcoin LONG prohibited" in str(reason)
    assert meta["btc_1h_pct_change"] == -2.10


@pytest.mark.asyncio
async def test_btc_dump_allows_altcoin_short():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-2.10, regime_direction="bearish")

    approved, reason, _ = await anchor.evaluate("NEAR/USDT", "short", btc_analysis=mock_btc)
    assert approved
    assert reason is None


@pytest.mark.asyncio
async def test_btc_rally_blocks_altcoin_short():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=2.50, regime_direction="bullish")

    approved, reason, meta = await anchor.evaluate("SUI/USDT", "short", btc_analysis=mock_btc)
    assert not approved
    assert "Altcoin SHORT prohibited" in str(reason)
    assert meta["btc_1h_pct_change"] == 2.50


@pytest.mark.asyncio
async def test_btc_bearish_regime_with_negative_drift_blocks_long():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-0.45, regime_direction="bearish")

    approved, reason, meta = await anchor.evaluate("DOT/USDT", "long", btc_analysis=mock_btc)
    assert not approved
    assert "BTC regime is bearish" in str(reason)
    assert "Altcoin LONG prohibited" in str(reason)


@pytest.mark.asyncio
async def test_btc_itself_is_exempt():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-3.50, regime_direction="bearish")

    approved, reason, meta = await anchor.evaluate("BTC/USDT", "long", btc_analysis=mock_btc)
    assert approved
    assert reason is None
    assert meta.get("exempt") is True


@pytest.mark.asyncio
async def test_non_crypto_is_exempt():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-3.50, regime_direction="bearish")

    for sym in ("XAUUSD", "EURUSD", "AAPL"):
        approved, reason, meta = await anchor.evaluate(sym, "long", btc_analysis=mock_btc)
        assert approved
        assert reason is None
        assert meta.get("exempt") is True


@pytest.mark.asyncio
async def test_explicit_stock_metadata_is_authoritative_even_for_usd_suffix():
    anchor = BTCMarketAnchor()
    approved, reason, meta = await anchor.evaluate(
        "COIN/USD", "long", market_type="stock", exchange="alpaca"
    )
    assert approved
    assert reason is None
    assert meta["status"] == "NOT_APPLICABLE"


@pytest.mark.asyncio
async def test_stale_btc_snapshot_fails_closed():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=0.0)
    mock_btc.valid_until = datetime.now(timezone.utc) - timedelta(seconds=1)

    approved, reason, meta = await anchor.evaluate(
        "ETH/USDT", "long", market_type="crypto", exchange="binance",
        btc_analysis=mock_btc,
    )
    assert not approved
    assert "stale" in str(reason).lower()
    assert meta["reason_code"] == "BTC_ANCHOR_STALE"


@pytest.mark.asyncio
async def test_disabled_filter_allows_all(monkeypatch):
    cfg = get_settings()
    monkeypatch.setattr(cfg, "btc_anchor_filter_enabled", False)

    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=-5.0, regime_direction="bearish")

    approved, reason, meta = await anchor.evaluate("NEAR/USDT", "long", btc_analysis=mock_btc)
    assert approved
    assert reason is None
    assert meta.get("reason") == "filter_disabled"


@pytest.mark.asyncio
async def test_btc_bearish_displacement_blocks_altcoin_long():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=0.0, regime_direction="neutral")
    mock_btc.signal.displacement = {"direction": "short"}

    approved, reason, meta = await anchor.evaluate("NEAR/USDT", "long", btc_analysis=mock_btc)
    assert not approved
    assert "bearish displacement" in str(reason).lower()
    assert meta["reason_code"] == "BTC_REGIME_CONFLICT"


@pytest.mark.asyncio
async def test_btc_heavy_selling_pressure_blocks_altcoin_long():
    anchor = BTCMarketAnchor()
    mock_btc = _make_mock_btc_analysis(pct_change_1h=0.0, regime_direction="neutral")
    mock_btc.signal.delta_ratio = -0.25

    approved, reason, meta = await anchor.evaluate("SUI/USDT", "long", btc_analysis=mock_btc)
    assert not approved
    assert "cvd selling pressure" in str(reason).lower()
    assert meta["reason_code"] == "BTC_REGIME_CONFLICT"


def test_validate_btc_anchor_helper():
    from app.services.btc_market_anchor import validate_btc_anchor

    # Altcoin with blocked systemic risk
    valid, reason = validate_btc_anchor("NEAR/USDT", "long", {"execution_allowed": False, "reason": "BTC dumping"})
    assert not valid
    assert "BTC dumping" in str(reason)

    # Altcoin with allowed systemic risk
    valid, reason = validate_btc_anchor("NEAR/USDT", "long", {"execution_allowed": True})
    assert valid
    assert reason is None

    # Exempt non-crypto asset
    valid, reason = validate_btc_anchor("XAUUSD", "long", {"execution_allowed": False, "reason": "BTC dumping"})
    assert valid
    assert reason is None
