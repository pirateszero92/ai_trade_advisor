"""
Composite Setup Detector (SETUP-001 to SETUP-004)
Orchestrates the 4 canonical deterministic setup detectors.
"""

from app.facts.models import MarketFacts
from app.market_state.classifier import MarketStateClassification
from app.setups.breakout_retest import BreakoutRetestDetector
from app.setups.candidate import OpportunityCandidate
from app.setups.continuation import ContinuationDetector
from app.setups.range_fade import RangeFadeDetector
from app.setups.reversal import ReversalDetector


class CompositeSetupDetector:
    """Evaluates the 4 canonical setup detectors in deterministic order."""

    def __init__(self) -> None:
        self.detectors = [
            ReversalDetector(),       # 1. Sweep Reversals (highest causal specificity)
            RangeFadeDetector(),      # 2. Range Fades (boundary tests)
            BreakoutRetestDetector(), # 3. Breakout Retests (breaker blocks)
            ContinuationDetector(),   # 4. Trend Continuations (OB/FVG retests)
        ]

    def detect_all(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> list[OpportunityCandidate]:
        """Returns all valid candidates found for this snapshot."""
        candidates = []
        for detector in self.detectors:
            candidate = detector.detect(facts, state)
            if candidate is not None and candidate.structural_rr >= 1.5:
                candidates.append(candidate)
        return candidates

    def detect_best(
        self,
        facts: MarketFacts,
        state: MarketStateClassification,
    ) -> OpportunityCandidate | None:
        """Selects the candidate with the highest structural R:R."""
        candidates = self.detect_all(facts, state)
        if not candidates:
            return None
        # Sort by structural R:R descending
        candidates.sort(key=lambda c: c.structural_rr, reverse=True)
        return candidates[0]
