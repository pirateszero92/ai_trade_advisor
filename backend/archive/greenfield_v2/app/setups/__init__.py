"""
Greenfield Setup Detectors Layer (SETUP-001 to SETUP-004)
"""

from app.setups.breakout_retest import BreakoutRetestDetector
from app.setups.candidate import OpportunityCandidate
from app.setups.composite import CompositeSetupDetector
from app.setups.continuation import ContinuationDetector
from app.setups.range_fade import RangeFadeDetector
from app.setups.reversal import ReversalDetector

__all__ = [
    "OpportunityCandidate",
    "ReversalDetector",
    "ContinuationDetector",
    "RangeFadeDetector",
    "BreakoutRetestDetector",
    "CompositeSetupDetector",
]
