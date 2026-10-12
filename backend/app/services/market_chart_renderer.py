"""Render a blind, snapshot-bound market chart for multimodal AI review.

The image deliberately excludes the deterministic direction, grade and trade
levels.  It is evidence for an independent market read, not a decorated order
ticket that anchors the model to Tri-Core's conclusion.
"""
from __future__ import annotations

import base64
import hashlib
import io
import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class RenderedMarketChart:
    image_base64: str
    mime_type: str
    sha256: str
    width: int
    height: int
    candle_count: int


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def render_market_chart(analysis, *, candle_limit: int = 80) -> RenderedMarketChart:
    """Return a compact PNG made only from the canonical execution snapshot."""
    required = {"open", "high", "low", "close", "volume"}
    frame = analysis.frame.tail(max(20, min(int(candle_limit), 120))).copy()
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError("Canonical OHLCV frame is unavailable for visual review")
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(required))
    if len(frame) < 10:
        raise ValueError("At least 10 complete candles are required for visual review")

    width, height = 1280, 800
    left, right, top = 72, 1188, 62
    price_bottom, volume_top, volume_bottom = 555, 585, 735
    background = (9, 15, 24)
    grid = (31, 43, 58)
    text = (185, 197, 211)
    bullish = (16, 185, 129)
    bearish = (244, 63, 94)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image, "RGBA")
    font = ImageFont.load_default()

    lows = frame["low"].astype(float)
    highs = frame["high"].astype(float)
    price_min = float(lows.min())
    price_max = float(highs.max())
    span = max(price_max - price_min, max(abs(price_max), 1.0) * 0.002)
    padding = span * 0.08
    price_min -= padding
    price_max += padding

    def py(price: float) -> float:
        ratio = (price_max - price) / max(price_max - price_min, 1e-12)
        return top + ratio * (price_bottom - top)

    count = len(frame)
    step = (right - left) / count
    body_width = max(3, int(step * 0.58))
    volume_max = max(float(frame["volume"].max()), 1.0)

    for idx in range(6):
        y = top + idx * (price_bottom - top) / 5
        draw.line((left, y, right, y), fill=grid + (190,), width=1)
        level = price_max - idx * (price_max - price_min) / 5
        draw.text((right + 8, y - 7), f"{level:,.6g}", fill=text + (255,), font=font)

    signal = analysis.signal
    visible_zones = []
    for zone in [
        *getattr(signal, "order_blocks", []),
        *getattr(signal, "fvgs", []),
        *getattr(signal, "breaker_blocks", []),
    ]:
        if getattr(zone, "mitigated", False):
            continue
        bottom = _finite(getattr(zone, "bottom", None), float("nan"))
        top_price = _finite(getattr(zone, "top", None), float("nan"))
        if not math.isfinite(bottom) or not math.isfinite(top_price):
            continue
        if top_price < price_min or bottom > price_max:
            continue
        visible_zones.append(zone)
    for zone in visible_zones[-8:]:
        zone_color = bullish if getattr(zone, "direction", "") == "bullish" else bearish
        y1 = py(_finite(zone.top))
        y2 = py(_finite(zone.bottom))
        draw.rectangle((left, min(y1, y2), right, max(y1, y2)), fill=zone_color + (28,), outline=zone_color + (115,))

    for idx, (_, row) in enumerate(frame.iterrows()):
        x = left + (idx + 0.5) * step
        open_price = float(row["open"])
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        color = bullish if close >= open_price else bearish
        draw.line((x, py(high), x, py(low)), fill=color + (255,), width=2)
        body_top = min(py(open_price), py(close))
        body_bottom = max(py(open_price), py(close))
        if body_bottom - body_top < 2:
            body_bottom = body_top + 2
        draw.rectangle(
            (x - body_width / 2, body_top, x + body_width / 2, body_bottom),
            fill=color + (245,),
        )
        volume_height = float(row["volume"]) / volume_max * (volume_bottom - volume_top)
        draw.rectangle(
            (x - body_width / 2, volume_bottom - volume_height, x + body_width / 2, volume_bottom),
            fill=color + (125,),
        )

    cvd_tail = [
        _finite(value, float("nan"))
        for value in (getattr(signal, "cvd_tail", None) or [])[-count:]
    ]
    cvd_tail = [value for value in cvd_tail if math.isfinite(value)]
    if len(cvd_tail) >= 2:
        cvd_min, cvd_max = min(cvd_tail), max(cvd_tail)
        cvd_span = max(cvd_max - cvd_min, 1.0)
        start_index = count - len(cvd_tail)
        points = []
        for offset, value in enumerate(cvd_tail):
            x = left + (start_index + offset + 0.5) * step
            y = volume_bottom - ((value - cvd_min) / cvd_span) * (volume_bottom - volume_top)
            points.append((x, y))
        draw.line(points, fill=(40, 126, 255, 255), width=3)

    last_close = float(frame["close"].iloc[-1])
    last_y = py(last_close)
    draw.line((left, last_y, right, last_y), fill=(18, 210, 171, 175), width=2)
    title = f"{analysis.symbol}  {analysis.timeframe}  CLOSED-CANDLE SNAPSHOT  {analysis.snapshot_id[:12]}"
    draw.text((left, 22), title, fill=(235, 241, 248, 255), font=font)
    draw.text((left, 755), "Volume bars + CVD line (blue) | active SMC zones only | no trade plan shown", fill=text + (255,), font=font)

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    payload = output.getvalue()
    return RenderedMarketChart(
        image_base64=base64.b64encode(payload).decode("ascii"),
        mime_type="image/png",
        sha256=hashlib.sha256(payload).hexdigest(),
        width=width,
        height=height,
        candle_count=count,
    )
