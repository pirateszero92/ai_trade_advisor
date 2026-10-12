"""
Greenfield Decision Orchestrator
Connects all decoupled layers in strict accordance with the Greenfield Blueprint:
Market Data -> Facts -> State -> Setup -> Opportunity -> AI Advisory -> Execution Plan -> Risk Allocator -> OMS.
"""

from datetime import datetime, timezone
from typing import Any
import pandas as pd

from app.ai.reviewer import AIReviewer, AIReviewResult
from app.domain.clock import Clock, SystemClock
from app.domain.execution import ExecutionDecision, ExecutionIntent
from app.domain.ids import generate_trace_id
from app.domain.opportunity import Opportunity, OpportunityState
from app.domain.order import Order
from app.domain.position import Position
from app.domain.reason_codes import ReasonCode
from app.domain.risk import RiskAssessment, RiskStage
from app.domain.trace import TraceRecord, TraceStage
from app.execution.planner import ExecutionPlanner
from app.facts.engine import MarketFactsEngine
from app.facts.models import MarketFacts
from app.market_data.candle_builder import build_closed_candle_frame
from app.market_data.quality import DataQualityMetadata
from app.market_data.quote import MarketQuote
from app.market_state.classifier import MarketStateClassification, MarketStateEngine
from app.oms.paper import PaperOMS
from app.opportunity.engine import OpportunityEngine
from app.opportunity.store import OpportunityStore
from app.positions.manager import PositionManager
from app.risk.allocator import RiskAllocator
from app.services.instrument_rules import InstrumentRules
from app.setups.composite import CompositeSetupDetector


class DecisionOrchestrator:
    """Central orchestrator for the opportunity-centric trading architecture."""

    def __init__(
        self,
        clock: Clock | None = None,
        store: OpportunityStore | None = None,
        facts_engine: MarketFactsEngine | None = None,
        state_engine: MarketStateEngine | None = None,
        setup_detector: CompositeSetupDetector | None = None,
        opportunity_engine: OpportunityEngine | None = None,
        execution_planner: ExecutionPlanner | None = None,
        risk_allocator: RiskAllocator | None = None,
        oms: PaperOMS | None = None,
        position_manager: PositionManager | None = None,
        ai_reviewer: AIReviewer | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self.store = store or OpportunityStore()
        self.facts_engine = facts_engine or MarketFactsEngine()
        self.state_engine = state_engine or MarketStateEngine()
        self.setup_detector = setup_detector or CompositeSetupDetector()
        self.opportunity_engine = opportunity_engine or OpportunityEngine(store=self.store, clock=self.clock)
        self.execution_planner = execution_planner or ExecutionPlanner()
        self.risk_allocator = risk_allocator or RiskAllocator()
        self.oms = oms or PaperOMS(clock=self.clock, risk_allocator=self.risk_allocator)
        self.position_manager = position_manager or PositionManager(clock=self.clock)
        self.ai_reviewer = ai_reviewer or AIReviewer()

        self.traces: list[TraceRecord] = []

    def _record_trace(
        self,
        opportunity_id: str,
        lineage_id: str,
        stage: TraceStage,
        reason_code: ReasonCode,
        facts: dict[str, Any],
    ) -> TraceRecord:
        record = TraceRecord(
            trace_id=generate_trace_id(stage.value),
            opportunity_id=opportunity_id,
            lineage_id=lineage_id,
            stage=stage,
            reason_code=reason_code,
            facts=facts,
            timestamp=self.clock.now(),
        )
        self.traces.append(record)
        return record

    def run_cycle(
        self,
        symbol: str,
        raw_ohlcv: pd.DataFrame,
        quote: MarketQuote,
        account_equity: float,
        daily_pnl_pct: float = 0.0,
        timeframe: str = "15m",
        market_type: str = "crypto",
        exchange: str = "binance",
        rules: InstrumentRules | None = None,
        quality_meta: DataQualityMetadata | None = None,
    ) -> dict[str, Any]:
        """
        Executes one full decision cycle on candle close:
        1. Closed-candle canonical filtering
        2. Market Facts calculation
        3. Market State classification
        4. Setup detection
        5. Opportunity creation & revalidation
        6. AI advisory review
        7. Execution Planning (Market / Limit / Wait / Cancel)
        8. Risk Allocation
        9. OMS Order Submission
        """
        now = self.clock.now()

        # Step 1: Closed-candle canonical frame (DATA-002)
        frame = build_closed_candle_frame(raw_ohlcv, timeframe=timeframe, now=now)
        if frame.empty:
            return {"status": "empty_frame", "opportunity": None, "order": None}

        # Step 2: Calculate Market Facts (FACT-001 to 008)
        facts = self.facts_engine.calculate(frame, symbol=symbol, timeframe=timeframe, quality_meta=quality_meta)

        # Step 3: Classify Market State (STATE-001)
        state = self.state_engine.classify(facts)

        # Step 3b: Revalidate existing pending limits for this symbol (OMS-004)
        cancelled_limits = self.oms.revalidate_pending_limits(
            facts_by_symbol={symbol: facts},
            daily_pnl_pct=daily_pnl_pct,
        )

        # Step 4: Detect Setup Candidates (SETUP-001 to 004)
        candidate = self.setup_detector.detect_best(facts, state)
        if candidate is None:
            return {
                "status": "no_setup",
                "state": state.state.value,
                "facts": facts,
                "opportunity": None,
                "order": None,
                "cancelled_limits": cancelled_limits,
            }

        # Step 5: Opportunity Creation & Storage (OPP-001 to 005)
        opportunity = self.opportunity_engine.create_from_candidate(
            candidate=candidate,
            symbol=symbol,
            timeframe=timeframe,
            market_type=market_type,
            exchange=exchange,
            data_quality=facts.data_quality.model_dump(),
            market_state=state.model_dump(),
        )
        self.opportunity_engine.revalidate(opportunity, facts)

        self._record_trace(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            stage=TraceStage.OPPORTUNITY_CREATED,
            reason_code=ReasonCode.OPPORTUNITY_CREATED,
            facts={"family": opportunity.family.value, "direction": opportunity.direction.value},
        )

        # Step 6: Advisory AI Review (AI-001 to 003, non-blocking)
        ai_review = self.ai_reviewer.review_facts_locally(opportunity, facts, state)

        # Step 7: Execution Planning (EXEC-001 to 006)
        intent = self.execution_planner.plan(
            opportunity=opportunity,
            quote=quote,
            atr=facts.volatility.atr,
            rules=rules,
            now=now,
        )

        self._record_trace(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            stage=TraceStage.EXECUTION_PLAN,
            reason_code=intent.reason_code,
            facts={
                "decision": intent.decision.value,
                "market_rr": intent.quoted_rr,
                "limit_rr": intent.limit_rr,
                "adverse_extension": intent.adverse_extension_atr,
            },
        )

        # Advance Opportunity state based on Execution Intent
        if intent.decision == ExecutionDecision.MARKET_NOW:
            if opportunity.can_transition_to(OpportunityState.MARKET_READY):
                opportunity.transition(OpportunityState.MARKET_READY, intent.reason_code)
        elif intent.decision == ExecutionDecision.PLACE_LIMIT:
            if opportunity.can_transition_to(OpportunityState.LIMIT_ARMED):
                opportunity.transition(OpportunityState.LIMIT_ARMED, intent.reason_code)
        elif intent.decision == ExecutionDecision.WAIT:
            if opportunity.can_transition_to(OpportunityState.WAITING_ENTRY):
                opportunity.transition(OpportunityState.WAITING_ENTRY, intent.reason_code)

        if not intent.is_executable:
            return {
                "status": "waiting_or_cancelled",
                "decision": intent.decision.value,
                "reason": intent.reasons,
                "opportunity": opportunity,
                "ai_review": ai_review,
                "order": None,
            }

        # Step 8: Risk Allocation (RISK-001 to 004)
        risk_stage = RiskStage.PRE_CHECK if intent.decision == ExecutionDecision.PLACE_LIMIT else RiskStage.FINAL_CHECK
        active_pos_dicts = [
            {"symbol": p.symbol, "direction": p.direction}
            for p in self.oms.get_open_positions()
        ]

        risk_assessment = self.risk_allocator.allocate(
            opportunity=opportunity,
            intent=intent,
            account_equity=account_equity,
            daily_pnl_pct=daily_pnl_pct,
            active_positions=active_pos_dicts,
            stage=risk_stage,
            rules=rules,
        )

        self._record_trace(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            stage=TraceStage.RISK_FINAL if risk_stage == RiskStage.FINAL_CHECK else TraceStage.RISK_PRECHECK,
            reason_code=risk_assessment.reason_code,
            facts={"approved": risk_assessment.approved, "position_size": risk_assessment.position_size},
        )

        if not risk_assessment.approved:
            if opportunity.can_transition_to(OpportunityState.RISK_REJECTED):
                opportunity.transition(OpportunityState.RISK_REJECTED, risk_assessment.reason_code)
            return {
                "status": "risk_rejected",
                "opportunity": opportunity,
                "risk_assessment": risk_assessment,
                "ai_review": ai_review,
                "order": None,
            }

        # Step 9: OMS Order Submission (OMS-001 to 005)
        idempotency_key = f"cycle_{opportunity.opportunity_id}_{intent.decision.value}"
        order = self.oms.submit_order(
            opportunity=opportunity,
            intent=intent,
            risk=risk_assessment,
            idempotency_key=idempotency_key,
        )

        self._record_trace(
            opportunity_id=opportunity.opportunity_id,
            lineage_id=opportunity.lineage_id,
            stage=TraceStage.OMS_SUBMIT,
            reason_code=ReasonCode.ORDER_SUBMITTED,
            facts={"client_order_id": order.client_order_id, "status": order.status.value},
        )

        return {
            "status": "order_placed",
            "opportunity": opportunity,
            "intent": intent,
            "risk_assessment": risk_assessment,
            "order": order,
            "ai_review": ai_review,
        }


# Global authoritative orchestrator singleton
orchestrator = DecisionOrchestrator()

