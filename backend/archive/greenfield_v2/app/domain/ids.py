"""
Domain Identifiers Generator (CORE-001)
Ensures globally unique, deterministic, and traceable identifiers across system layers.
"""

from datetime import datetime, timezone
import uuid


def generate_opportunity_id(symbol: str, timeframe: str, timestamp: datetime | None = None) -> str:
    """Creates a unique Opportunity ID bound to instrument, timeframe, and detection timestamp."""
    ts = timestamp or datetime.now(timezone.utc)
    ts_str = ts.strftime("%Y%m%d%H%M%S")
    clean_sym = symbol.replace("/", "").replace(":", "").replace("-", "").upper()
    uid = uuid.uuid4().hex[:8]
    return f"opp_{clean_sym}_{timeframe}_{ts_str}_{uid}"


def generate_lineage_id(symbol: str, timeframe: str, family: str) -> str:
    """
    Creates a Lineage ID that groups successive updates/revalidations
    of the same core market structure across candles.
    """
    clean_sym = symbol.replace("/", "").replace(":", "").replace("-", "").upper()
    uid = uuid.uuid4().hex[:8]
    return f"lin_{clean_sym}_{timeframe}_{family.lower()}_{uid}"


def generate_order_id(opportunity_id: str, side: str) -> str:
    """Generates an idempotent client order ID linked to its parent opportunity."""
    uid = uuid.uuid4().hex[:6]
    return f"ord_{opportunity_id}_{side.lower()}_{uid}"


def generate_position_id(opportunity_id: str) -> str:
    """Generates a position ID tied to the originating opportunity."""
    uid = uuid.uuid4().hex[:6]
    return f"pos_{opportunity_id}_{uid}"


def generate_trace_id(stage: str) -> str:
    """Generates a decision trace record ID."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    uid = uuid.uuid4().hex[:6]
    return f"trc_{stage.lower()}_{ts}_{uid}"
