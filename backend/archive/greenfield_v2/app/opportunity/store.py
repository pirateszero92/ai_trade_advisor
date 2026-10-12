"""
Opportunity Store (OPP-002)
Manages storage, retrieval, and active query sets for persistent opportunities.
"""

from typing import Any
from app.domain.opportunity import Opportunity, OpportunityState


class OpportunityStore:
    """In-memory persistent store for Opportunities."""

    def __init__(self) -> None:
        self._opportunities: dict[str, Opportunity] = {}
        self._by_symbol: dict[str, set[str]] = {}

    def save(self, opportunity: Opportunity) -> None:
        self._opportunities[opportunity.opportunity_id] = opportunity
        self._by_symbol.setdefault(opportunity.symbol, set()).add(opportunity.opportunity_id)

    def get(self, opportunity_id: str) -> Opportunity | None:
        return self._opportunities.get(opportunity_id)

    def get_active_by_symbol(self, symbol: str) -> list[Opportunity]:
        opp_ids = self._by_symbol.get(symbol, set())
        active = []
        for opp_id in opp_ids:
            opp = self._opportunities.get(opp_id)
            if opp and not opp.is_terminal:
                active.append(opp)
        return active

    def all_active(self) -> list[Opportunity]:
        return [opp for opp in self._opportunities.values() if not opp.is_terminal]

    def all(self) -> list[Opportunity]:
        return list(self._opportunities.values())

    def clear(self) -> None:
        self._opportunities.clear()
        self._by_symbol.clear()
