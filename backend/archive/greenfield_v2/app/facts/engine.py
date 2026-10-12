"""
Market Facts Engine (FACT-001 to FACT-008)
Authoritative calculator of deterministic market facts from closed-candle frames.
Never votes on trade direction or bypasses execution layers.
"""

from datetime import datetime, timezone
import math
import pandas as pd

from app.domain.opportunity import CausalZone
from app.engines.smc_engine import SMCEngine
from app.facts.models import (
    FlowFact,
    LiquidityFact,
    MarketFacts,
    StructureBreakFact,
    StructureFact,
    SwingFact,
    VolatilityFact,
    ZonesFact,
)
from app.market_data.quality import DataQuality, DataQualityMetadata


class MarketFactsEngine:
    """Calculates deterministic structural, liquidity, zone, flow, and volatility facts."""

    def __init__(self, smc_engine: SMCEngine | None = None) -> None:
        self.smc = smc_engine or SMCEngine()

    def calculate(
        self,
        frame: pd.DataFrame,
        symbol: str,
        timeframe: str = "15m",
        quality_meta: DataQualityMetadata | None = None,
    ) -> MarketFacts:
        """
        Executes deterministic calculations on a canonical closed-candle frame.
        """
        if frame.empty or len(frame) < 15:
            now_dt = datetime.now(timezone.utc)
            return MarketFacts(
                symbol=symbol,
                timeframe=timeframe,
                candle_close_time=now_dt,
                current_price=0.0,
                structure=StructureFact(),
                liquidity=LiquidityFact(),
                zones=ZonesFact(),
                flow=FlowFact(),
                volatility=VolatilityFact(),
                data_quality=quality_meta or DataQualityMetadata(),
            )

        # Run underlying SMC engine analysis on closed candles
        smc_sig = self.smc.analyze(frame, symbol=symbol, timeframe=timeframe)

        last_ts = frame.index[-1]
        candle_close_time = last_ts.to_pydatetime() if isinstance(last_ts, pd.Timestamp) else datetime.now(timezone.utc)
        if candle_close_time.tzinfo is None:
            candle_close_time = candle_close_time.replace(tzinfo=timezone.utc)

        current_price = float(frame["close"].iloc[-1])

        # 1. Structure Facts
        swings_high = [
            SwingFact(
                index=s.index,
                price=s.price,
                kind="high",
                timestamp=s.timestamp.to_pydatetime() if isinstance(s.timestamp, pd.Timestamp) else datetime.now(timezone.utc),
                level_id=s.level_id,
            )
            for s in smc_sig.swing_highs
        ]
        swings_low = [
            SwingFact(
                index=s.index,
                price=s.price,
                kind="low",
                timestamp=s.timestamp.to_pydatetime() if isinstance(s.timestamp, pd.Timestamp) else datetime.now(timezone.utc),
                level_id=s.level_id,
            )
            for s in smc_sig.swing_lows
        ]

        breaks = [
            StructureBreakFact(
                tag=b.tag,
                kind=b.kind,
                direction=b.direction,
                level=b.level,
                pivot_index=b.pivot_index,
                break_index=b.break_index,
            )
            for b in smc_sig.swing_structures + smc_sig.internal_structures
        ]

        structure = StructureFact(
            bias=smc_sig.bias,
            swing_highs=swings_high,
            swing_lows=swings_low,
            breaks=breaks,
            has_bos=smc_sig.bos,
            has_choch=smc_sig.choch,
            displacement_detected=bool(smc_sig.displacement.get("detected", False)),
        )

        # 2. Liquidity Facts
        liquidity = LiquidityFact(
            equal_highs=smc_sig.equal_highs,
            equal_lows=smc_sig.equal_lows,
            liquidity_swept=smc_sig.liquidity_swept,
            sweep_direction=smc_sig.sweep_direction,
            sweep_price=smc_sig.sweep_price,
            sweep_age_bars=int(smc_sig.liquidity_sweep.get("age_bars", 0)),
        )

        # 3. Zone Facts
        active_obs = [
            CausalZone(
                zone_id=z.zone_id,
                kind="ob",
                top=z.top,
                bottom=z.bottom,
                active=not z.mitigated,
            )
            for z in smc_sig.order_blocks if not z.mitigated
        ]
        active_fvgs = [
            CausalZone(
                zone_id=z.zone_id,
                kind="fvg",
                top=z.top,
                bottom=z.bottom,
                active=not z.mitigated,
            )
            for z in smc_sig.fvgs if not z.mitigated
        ]
        active_breakers = [
            CausalZone(
                zone_id=z.zone_id,
                kind="breaker",
                top=z.top,
                bottom=z.bottom,
                active=not z.mitigated,
            )
            for z in smc_sig.breaker_blocks if not z.mitigated
        ]

        prem = smc_sig.premium_zone or {}
        disc = smc_sig.discount_zone or {}

        zones = ZonesFact(
            active_order_blocks=active_obs,
            active_fvgs=active_fvgs,
            active_breakers=active_breakers,
            equilibrium=smc_sig.equilibrium,
            premium_boundary=float(prem.get("top", 0.0)),
            discount_boundary=float(disc.get("bottom", 0.0)),
            in_premium=smc_sig.in_premium,
            in_discount=smc_sig.in_discount,
        )

        # 4. Flow Facts
        flow_quality = (
            DataQuality.EXCHANGE_NATIVE
            if smc_sig.volume_quality == "exchange_aggressor"
            else DataQuality.DERIVED
        )
        flow = FlowFact(
            cvd_direction=(
                "bullish" if smc_sig.cvd > 0
                else ("bearish" if smc_sig.cvd < 0 else "neutral")
            ),
            cvd_divergence=smc_sig.cvd_divergence,
            delta_absorption=smc_sig.delta_absorption,
            delta_absorption_type=smc_sig.delta_absorption_type,
            current_delta=smc_sig.volume_delta,
            quality=flow_quality,
        )

        # 5. Volatility Facts
        # Calculate ATR(14) on closed candles
        highs = frame["high"].to_numpy()
        lows = frame["low"].to_numpy()
        closes = frame["close"].to_numpy()
        prev_closes = closes[:-1]
        tr = [highs[0] - lows[0]]
        for i in range(1, len(closes)):
            tr.append(max(highs[i] - lows[i], abs(highs[i] - prev_closes[i - 1]), abs(lows[i] - prev_closes[i - 1])))
        tr_series = pd.Series(tr)
        atr_val = float(tr_series.rolling(14).mean().iloc[-1]) if len(tr_series) >= 14 else float(tr_series.mean())
        if not math.isfinite(atr_val) or atr_val <= 0:
            atr_val = current_price * 0.01

        atr_pct = float(tr_series.rank(pct=True).iloc[-1] * 100.0) if len(tr_series) >= 20 else 50.0

        volatility = VolatilityFact(
            atr=round(atr_val, 6),
            realized_volatility=round(float(frame["close"].pct_change().std() * math.sqrt(365 * 24 * 4) * 100.0) if len(frame) > 20 else 0.0, 2),
            atr_percentile=round(atr_pct, 1),
            is_compression=smc_sig.squeeze_status == "squeeze_on",
            is_expansion=smc_sig.squeeze_status == "squeeze_fire",
        )

        return MarketFacts(
            symbol=symbol,
            timeframe=timeframe,
            candle_close_time=candle_close_time,
            current_price=current_price,
            structure=structure,
            liquidity=liquidity,
            zones=zones,
            flow=flow,
            volatility=volatility,
            data_quality=quality_meta or DataQualityMetadata(),
        )
