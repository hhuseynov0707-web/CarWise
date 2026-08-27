"""Negotiation intelligence (spec §28).

The rule that shapes this module: *do not generate arbitrary negotiation
numbers*. Every figure produced here traces to something measured elsewhere in
the analysis — the fitted fair-market range, the unexplained portion of the
price gap, the observed price-reduction behaviour of comparable listings, or
the vehicle's own listing history.

Where a number cannot be grounded, this engine says so instead of inventing one.
The most common such case is the asking-to-settled discount: until the platform
has contributed transaction data (spec §9), we genuinely do not know how far
sellers move, and the honest substitute is the *observed price-reduction
behaviour of comparable listings*, which we can measure directly from listing
history.

Pure computation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import DealRating, RiskSeverity, ValuationOutcome
from app.domain.market import SubjectVehicle
from app.domain.money import Money
from app.engines.comparables.engine import ComparableSet
from app.engines.rating.engine import PricePosition
from app.engines.risk.engine import RiskAssessment
from app.engines.stats import median
from app.engines.valuation.engine import Valuation

#: Minimum comparables with observable price history before we quote the
#: market's typical reduction behaviour.
MIN_REDUCTION_SAMPLE = 6

#: Days on market past which a seller is measurably harder to sell to nobody
#: else — i.e. leverage accrues to the buyer.
LEVERAGE_DAYS_MODERATE = 45
LEVERAGE_DAYS_STRONG = 90


@dataclass(frozen=True, slots=True)
class LeveragePoint:
    """One evidence-backed argument a buyer can actually make."""

    title: str
    evidence: str
    strength: str
    """``"strong"``, ``"moderate"`` or ``"weak"`` — how much weight it carries."""

    monetary_basis_azn: float | None = None
    """Money this argument can be quantified at, when it can be at all."""


@dataclass(frozen=True, slots=True)
class NegotiationStrategy:
    """Evidence-derived negotiating positions.

    All three prices are anchored to the computed market range, never to a
    percentage pulled from the air.
    """

    available: bool
    opening_offer: Money | None = None
    target_range_low: Money | None = None
    target_range_high: Money | None = None
    walk_away_above: Money | None = None
    """Above this the vehicle stops being competitive against the alternatives
    we can actually see in the market."""

    leverage: tuple[LeveragePoint, ...] = ()
    observed_market_reduction_pct: float | None = None
    """Median price cut among comparable listings that moved their price."""

    reduction_sample_size: int = 0
    rationale: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    posture: str = ""
    """One-line framing of the buyer's position."""


@dataclass
class NegotiationEngine:
    """Derives negotiating positions from the analysis already performed."""

    def build(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        position: PricePosition,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> NegotiationStrategy:
        if valuation.outcome is not ValuationOutcome.OK or valuation.central_estimate is None:
            return NegotiationStrategy(
                available=False,
                unavailable_reason=(
                    "Without a market range there is no evidence base for a negotiating "
                    "position, and a number invented here would be worse than none."
                ),
            )
        if subject.asking_price is None:
            return NegotiationStrategy(
                available=False,
                unavailable_reason="No asking price was provided to negotiate against.",
            )

        asking = subject.asking_price.as_float()
        central = valuation.central_estimate.as_float()
        low = valuation.fair_market_low.as_float() if valuation.fair_market_low else central
        high = valuation.fair_market_high.as_float() if valuation.fair_market_high else central

        reduction_pct, reduction_n = self._observed_reductions(comparables)
        leverage = self._leverage(subject, comparables, valuation, position, risk, as_of)

        opening, target_low, target_high, rationale, posture = self._positions(
            asking, central, low, high, position, leverage, reduction_pct
        )

        return NegotiationStrategy(
            available=True,
            opening_offer=Money.azn(round(opening)),
            target_range_low=Money.azn(round(target_low)),
            target_range_high=Money.azn(round(target_high)),
            walk_away_above=Money.azn(round(high)),
            leverage=leverage,
            observed_market_reduction_pct=reduction_pct,
            reduction_sample_size=reduction_n,
            rationale=rationale,
            posture=posture,
        )

    # --- positions ---------------------------------------------------------

    def _positions(
        self,
        asking: float,
        central: float,
        low: float,
        high: float,
        position: PricePosition,
        leverage: tuple[LeveragePoint, ...],
        reduction_pct: float | None,
    ) -> tuple[float, float, float, tuple[str, ...], str]:
        """Anchor the three positions to the market range.

        When the asking price already sits at or below the central estimate,
        price is not the problem to solve and the strategy says so. Pushing for
        a further discount on an already-good price, while skipping the
        inspection, is how buyers lose money on this market.
        """
        rationale: list[str] = []

        if asking <= central:
            posture = (
                "The asking price is already at or below the estimated market value. "
                "The priority here is verification, not discount."
            )
            # The band must never invert. When the asking price already sits
            # below the fair range there is no market-based case for a further
            # discount, and the honest position is the asking price itself.
            target_high = asking
            target_low = min(asking, max(low, asking * 0.95))
            opening = min(target_low, asking)
            rationale.append(
                f"Asking {asking:,.0f} AZN is already at or below the central estimate of "
                f"{central:,.0f} AZN, so there is limited headroom to argue on market data alone."
            )
            rationale.append(
                "Any further movement will come from specific findings — inspection "
                "results, missing service history, or wear items due for replacement."
            )
        else:
            posture = (
                "The asking price sits above the estimated market value, and the market "
                "data itself supports a lower number."
            )
            target_high = central
            target_low = max(low, central - (central - low) * 0.5)
            # Open below the target so there is room to settle at it — but never
            # below the bottom of the fair range, which would be an offer the
            # evidence cannot defend.
            opening = max(low, target_low - (asking - central) * 0.35)
            rationale.append(
                f"Asking {asking:,.0f} AZN is {position.difference_pct:+.1f}% against a "
                f"central estimate of {central:,.0f} AZN."
            )
            rationale.append(
                f"The fair-market range for this configuration is {low:,.0f}–{high:,.0f} AZN, "
                f"which is the defensible band for any offer."
            )

        if reduction_pct is not None:
            rationale.append(
                f"Comparable listings that changed price moved by a median of "
                f"{reduction_pct:+.1f}%, which indicates the room sellers in this segment "
                f"have actually been giving."
            )
        else:
            rationale.append(
                "Too few comparable listings have observable price history to say how far "
                "sellers in this segment typically move."
            )

        strong = [p for p in leverage if p.strength == "strong"]
        if strong:
            rationale.append(
                f"{len(strong)} strong evidence point{'s' if len(strong) != 1 else ''} "
                f"support{'' if len(strong) != 1 else 's'} a lower price; each is listed "
                f"with the fact behind it."
            )

        return opening, target_low, target_high, tuple(rationale), posture

    # --- evidence ----------------------------------------------------------

    def _observed_reductions(self, comparables: ComparableSet) -> tuple[float | None, int]:
        """Median observed price movement among comparables that changed price.

        This is the closest honest proxy we have for negotiating room until real
        transaction data exists. It measures what sellers in this exact segment
        have actually done, rather than assuming a discount convention.
        """
        moves = [
            m.listing.total_price_change_pct
            for m in comparables.matches
            if m.listing.has_price_history and m.listing.total_price_change_pct is not None
        ]
        moves = [m for m in moves if m != 0.0]
        if len(moves) < MIN_REDUCTION_SAMPLE:
            return None, len(moves)
        return round(median(moves), 1), len(moves)

    def _leverage(
        self,
        subject: SubjectVehicle,
        comparables: ComparableSet,
        valuation: Valuation,
        position: PricePosition,
        risk: RiskAssessment,
        as_of: datetime,
    ) -> tuple[LeveragePoint, ...]:
        out: list[LeveragePoint] = []

        days = subject.days_listed(as_of)
        if days is not None and days >= LEVERAGE_DAYS_MODERATE:
            out.append(
                LeveragePoint(
                    title="The car has not sold in a long time",
                    evidence=(
                        f"Listed for {days} days. Cars priced in line with the market in "
                        f"this segment typically move faster."
                    ),
                    strength="strong" if days >= LEVERAGE_DAYS_STRONG else "moderate",
                )
            )

        changes = subject.price_change_count
        if changes:
            move = subject.total_price_change_pct
            out.append(
                LeveragePoint(
                    title="The seller has already reduced the price",
                    evidence=(
                        f"Price changed {changes} time{'s' if changes != 1 else ''}"
                        + (f", a net {move:+.1f}% from the original ask." if move else ".")
                        + " A seller who has moved once has demonstrated they will move."
                    ),
                    strength="strong" if changes >= 2 else "moderate",
                )
            )

        if position.difference_azn and position.difference_azn > 0:
            out.append(
                LeveragePoint(
                    title="Comparable cars are cheaper",
                    evidence=(
                        f"{comparables.size} comparable listings were analysed; this one asks "
                        f"{position.difference_pct:+.1f}% against the central estimate. "
                        f"Roughly {100 - (position.percentile or 0):.0f}% of them ask less."
                    ),
                    strength="strong",
                    monetary_basis_azn=float(position.difference_azn),
                )
            )

        for signal in risk.signals:
            if signal.severity in (RiskSeverity.HIGH, RiskSeverity.CRITICAL):
                out.append(
                    LeveragePoint(
                        title=signal.title,
                        evidence=(
                            f"{signal.evidence[0]} Resolving this is a cost or a risk the "
                            f"buyer would carry."
                        ),
                        strength="strong",
                    )
                )
            elif signal.severity is RiskSeverity.MODERATE and len(out) < 6:
                out.append(
                    LeveragePoint(
                        title=signal.title,
                        evidence=signal.evidence[0],
                        strength="moderate",
                    )
                )

        if not subject.service_records_provided:
            out.append(
                LeveragePoint(
                    title="No service history was supplied",
                    evidence=(
                        "An undocumented maintenance history transfers the cost of any "
                        "overdue service to the buyer, and reduces what the car will "
                        "fetch on resale."
                    ),
                    strength="moderate",
                )
            )

        if comparables.size >= 15:
            out.append(
                LeveragePoint(
                    title="You have alternatives",
                    evidence=(
                        f"{comparables.size} comparable vehicles are on the market. Walking "
                        f"away is a credible position, and both sides know it."
                    ),
                    strength="moderate",
                )
            )

        return tuple(out[:8])


def negotiation_summary(strategy: NegotiationStrategy, rating: DealRating) -> str:
    """One-paragraph framing, used when the language model is unavailable."""
    if not strategy.available:
        return strategy.unavailable_reason or "No negotiation guidance is available."

    parts = [strategy.posture]
    if strategy.opening_offer and strategy.target_range_low and strategy.target_range_high:
        parts.append(
            f"An opening position of {strategy.opening_offer.format()} leaves room to settle "
            f"in the {strategy.target_range_low.format()}–{strategy.target_range_high.format()} "
            f"band, which the comparable evidence supports."
        )
    if strategy.walk_away_above:
        parts.append(
            f"Above {strategy.walk_away_above.format()} the vehicle stops being competitive "
            f"against the alternatives currently listed."
        )
    if rating is DealRating.SUSPICIOUSLY_CHEAP:
        parts.append(
            "Given how far below the market this is priced, establishing why should come "
            "before any discussion of price."
        )
    return " ".join(parts)
