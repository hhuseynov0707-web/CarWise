"""Analysis orchestration.

Per the layer rules in audit §8, this module **wires** — it does not compute.
Every number comes from an engine; every listing comes from a port. If a
calculation appears here in future, it belongs in an engine instead, where it
can be unit-tested without a database.

The pipeline is spec §51 Workflow C, and spec §70's user journey, in one place:

    resolve identity -> fetch candidates -> select comparables -> value
    -> assess risk -> score confidence -> rate -> negotiate -> plan inspection
    -> aggregate evidence -> narrate -> return
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.adapters.llm.service import NarrativeResult, ReasoningService
from app.domain.identity import VehicleConfiguration
from app.domain.market import MarketListing, SubjectVehicle, TransactionObservation
from app.engines.comparables.engine import ComparableEngine, SelectionPolicy
from app.engines.confidence.engine import ConfidenceEngine, describe_improvements
from app.engines.evidence.bundle import (
    AnalysisResult,
    build_evidence_bundle,
    build_limitations,
)
from app.engines.inspection.engine import InspectionEngine
from app.engines.negotiation.engine import NegotiationEngine
from app.engines.rating.engine import RatingEngine, candidate_explanations
from app.engines.risk.engine import RiskEngine
from app.engines.valuation.engine import ValuationEngine


class MarketRepository(Protocol):
    """Port for market data. Implemented by the database layer.

    Declared here as a Protocol rather than imported from ``app.db`` so that the
    service layer does not depend on a concrete storage choice — spec §72's
    requirement that the market data layer be replaceable.
    """

    async def candidate_listings(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
        limit: int,
    ) -> Sequence[MarketListing]:
        """Listings that could plausibly be comparables.

        Implementations should query on the *model* key, not the exact
        configuration: filtering to an exact match here would prevent the
        comparable engine from widening, which is its job, not the database's.
        """
        ...

    async def transaction_observations(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
    ) -> Sequence[TransactionObservation]:
        ...


@dataclass(frozen=True, slots=True)
class Analysis:
    """A completed analysis and its narrative."""

    result: AnalysisResult
    narrative: NarrativeResult
    evidence_bundle: dict[str, object]


@dataclass
class AnalysisService:
    """Runs the full analysis pipeline for one subject vehicle."""

    repository: MarketRepository
    reasoning: ReasoningService
    comparables: ComparableEngine
    valuation: ValuationEngine
    risk: RiskEngine
    confidence: ConfidenceEngine
    rating: RatingEngine
    negotiation: NegotiationEngine
    inspection: InspectionEngine
    candidate_limit: int = 2000

    @classmethod
    def build(
        cls,
        repository: MarketRepository,
        reasoning: ReasoningService,
        policy: SelectionPolicy | None = None,
    ) -> AnalysisService:
        selection = policy or SelectionPolicy()
        return cls(
            repository=repository,
            reasoning=reasoning,
            comparables=ComparableEngine(policy=selection),
            valuation=ValuationEngine(min_sample=selection.min_sample),
            risk=RiskEngine(),
            confidence=ConfidenceEngine(),
            rating=RatingEngine(),
            negotiation=NegotiationEngine(),
            inspection=InspectionEngine(),
        )

    async def analyse(
        self,
        subject: SubjectVehicle,
        as_of: datetime,
        language: str = "en",
        narrate: bool = True,
    ) -> Analysis:
        candidates = await self.repository.candidate_listings(
            subject.configuration,
            as_of,
            self.comparables.policy.observation_window_days,
            self.candidate_limit,
        )
        transactions = await self.repository.transaction_observations(
            subject.configuration,
            as_of,
            self.comparables.policy.observation_window_days,
        )

        result = self.compute(subject, candidates, transactions, as_of)
        bundle = build_evidence_bundle(result)

        narrative = (
            await self.reasoning.narrate(bundle, language)
            if narrate
            else await ReasoningService(None, enabled=False).narrate(bundle, language)
        )
        return Analysis(result=result, narrative=narrative, evidence_bundle=bundle)

    def compute(
        self,
        subject: SubjectVehicle,
        candidates: Sequence[MarketListing],
        transactions: Sequence[TransactionObservation],
        as_of: datetime,
    ) -> AnalysisResult:
        """The pure analytical pipeline.

        Separated from :meth:`analyse` so it can be exercised in tests with
        plain fixtures — no database, no network, no language model. This is
        what spec §72 means by the valuation engine being independently
        testable.
        """
        conflicts = tuple(subject.ledger.all_conflicts(as_of))

        comparable_set = self.comparables.select(subject, candidates, as_of)
        valuation = self.valuation.estimate(subject, comparable_set, as_of, transactions)
        risk = self.risk.assess(subject, comparable_set, valuation, as_of, conflicts)
        confidence = self.confidence.assess(subject, comparable_set, valuation, as_of)
        position = self.rating.evaluate(subject, comparable_set, valuation, risk, as_of)
        negotiation = self.negotiation.build(
            subject, comparable_set, valuation, position, risk, as_of
        )
        inspection = self.inspection.build(subject, comparable_set, position, risk, as_of)

        return AnalysisResult(
            analysis_id=f"an_{uuid.uuid4().hex[:16]}",
            generated_at=as_of,
            subject=subject,
            comparables=comparable_set,
            valuation=valuation,
            position=position,
            risk=risk,
            confidence=confidence,
            negotiation=negotiation,
            inspection=inspection,
            conflicts=conflicts,
            explanations=candidate_explanations(position, risk, subject, as_of),
            limitations=build_limitations(valuation, confidence, comparable_set, subject),
            improvements=tuple(describe_improvements(confidence, subject)),
        )


class InMemoryMarketRepository:
    """Repository backed by a list. Used in tests and for local development.

    Its existence is a check on the port's design: if the analysis pipeline can
    run against a plain list as easily as against PostgreSQL, then the market
    data layer really is replaceable rather than nominally so.
    """

    def __init__(
        self,
        listings: Sequence[MarketListing] = (),
        transactions: Sequence[TransactionObservation] = (),
    ) -> None:
        self._listings = list(listings)
        self._transactions = list(transactions)

    async def candidate_listings(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
        limit: int,
    ) -> Sequence[MarketListing]:
        model_key = configuration.model_key
        return [
            listing
            for listing in self._listings
            if listing.configuration.model_key == model_key
            and listing.age_days(as_of) <= window_days
        ][:limit]

    async def transaction_observations(
        self,
        configuration: VehicleConfiguration,
        as_of: datetime,
        window_days: int,
    ) -> Sequence[TransactionObservation]:
        model_key = configuration.model_key
        return [
            observation
            for observation in self._transactions
            if observation.configuration.model_key == model_key
        ]

    def add(self, listing: MarketListing) -> None:
        self._listings.append(listing)
