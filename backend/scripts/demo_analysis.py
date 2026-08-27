"""Offline demonstration of the analysis pipeline.

    python scripts/demo_analysis.py

Runs the full chain — comparable selection, valuation, risk, confidence,
rating, negotiation, inspection, narrative — with no database, no network and
no API key, and prints the report.

**The market it runs against is synthetic.** It is generated from a known price
model so that the output can be checked against a ground truth we control; it
is not Azerbaijani market data and nothing here should be read as one. That is
the point of the exercise: it demonstrates the machinery, and it demonstrates
that the machinery gets the right answer when the right answer is known.

Every figure printed below is computed by the engines. The narrative is
generated deterministically from that computed evidence, with the language model
switched off entirely — which is the invariant from docs/00 §5, exercised here
rather than asserted.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.llm.service import ReasoningService  # noqa: E402
from app.engines.evidence.bundle import build_evidence_bundle  # noqa: E402
from app.services.analysis import (  # noqa: E402
    AnalysisService,
    InMemoryMarketRepository,
)
from tests.factories import (  # noqa: E402
    REFERENCE_NOW,
    expected_price,
    make_subject,
    synthetic_market,
)

RULE = "=" * 78
THIN = "-" * 78


def heading(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


def section(text: str) -> None:
    print(f"\n{text}\n{THIN}")


async def main() -> int:
    listings, truth = synthetic_market(count=60, mileage_spread=110_000)
    service = AnalysisService.build(
        InMemoryMarketRepository(listings),
        ReasoningService(None, enabled=False),
    )

    subject = make_subject(
        mileage_km=168_000,
        asking_price=32_500,
        description="Ideal vəziyyətdə, vurulub, rənglənib",
    )

    analysis = await service.analyse(subject, REFERENCE_NOW)
    result, narrative = analysis.result, analysis.narrative
    valuation, position, risk, confidence = (
        result.valuation,
        result.position,
        result.risk,
        result.confidence,
    )

    heading(f"  {result.headline}")
    print("  SYNTHETIC MARKET — generated from a known price model for")
    print("  demonstration. Not Azerbaijani market data.")

    # --- what the first screen must answer (spec §54) ----------------------
    section("MARKET POSITION")
    print(f"  Fair market range   {valuation.fair_market_low} – {valuation.fair_market_high}")
    print(f"  Central estimate    {valuation.central_estimate}")
    print(f"  Asking price        {position.asking_price}")
    print(f"  Market position     {position.label.upper()}  ({position.difference_pct:+.1f}%)")
    print(f"  Risk indicators     {risk.score}/100 — {risk.band_label}")
    print(f"  Confidence          {confidence.percent}% ({confidence.band.lower()})")
    print(f"  Price basis         {valuation.basis.value} — not confirmed sale prices")

    # --- ground truth check ------------------------------------------------
    section("ACCURACY AGAINST THE KNOWN GROUND TRUTH")
    true_value = expected_price(truth, 168_000, 2019)
    error = (valuation.central_estimate.as_float() - true_value) / true_value * 100
    inside = (
        valuation.fair_market_low.as_float()
        <= true_value
        <= valuation.fair_market_high.as_float()
    )
    print(f"  True value of this vehicle    {true_value:>12,.0f} AZN")
    print(f"  Engine's central estimate     {valuation.central_estimate.as_float():>12,.0f} AZN")
    print(f"  Error                         {error:>+11.2f}%")
    print(f"  True value inside the range   {'yes' if inside else 'NO'}")
    print()
    mileage = next((a for a in valuation.adjustments if a.name == "mileage"), None)
    if mileage and mileage.slope:
        print(f"  True mileage slope (hidden)   {truth['mileage_slope']:>12.4f} AZN/km")
        print(f"  Slope recovered from market   {mileage.slope:>12.4f} AZN/km")

    # --- why (spec §55) ----------------------------------------------------
    section("WHY THIS RATING")
    for line in position.rationale:
        print(f"  • {line}")

    if position.gap_analysis:
        gap = position.gap_analysis
        section("WHERE THE PRICE DIFFERENCE COMES FROM")
        print(f"  Comparable median            {gap.reference_median_azn:>12,.0f} AZN")
        print(f"  This car asks                {gap.total_gap_azn:>+12,.0f} AZN vs that median")
        print()
        for component in gap.components:
            print(f"    {component.label:<34}{component.amount_azn:>+10,.0f} AZN")
        print(f"    {'-' * 44}")
        print(f"    {'explained by measurable factors':<34}{gap.explained_azn:>+10,.0f} AZN")
        print(f"    {'UNEXPLAINED':<34}{gap.unexplained_azn:>+10,.0f} AZN")
        print(f"\n  {gap.explained_share:.0%} of the difference is accounted for.")

    # --- risk (spec §21, §69) ---------------------------------------------
    section("RISK INDICATORS — with what each contributes")
    for contribution in risk.contributions:
        print(f"  {contribution.marginal_points:>5.1f} pts  [{contribution.severity.value:<8}] {contribution.title}")
    print()
    for signal in risk.by_severity()[:3]:
        print(f"  {signal.title}")
        for evidence in signal.evidence:
            print(f"      evidence: {evidence}")
        print(f"      verify:   {signal.recommended_verification}")
        print()

    # --- confidence (spec §48) --------------------------------------------
    section("CONFIDENCE — every point accounted for")
    for component in confidence.components:
        print(f"  {component.contribution_points:>5.1f} pts  {component.label:<30} {component.explanation}")
    print(f"\n  {'total':>5}     {confidence.percent} / 100")
    print(f"  Calibrated against verified outcomes: {'yes' if confidence.calibrated else 'no'}")

    # --- actionable output -------------------------------------------------
    section("WHAT TO ASK THE SELLER — generated from the findings above")
    for question in result.inspection.questions[:5]:
        print(f"  • {question.question}")
        print(f"    {question.why}")

    section("INSPECTION PRIORITIES")
    for item in result.inspection.high[:6]:
        print(f"  HIGH    {item.item}")
        print(f"          triggered by: {item.triggered_by}")

    if result.negotiation.available:
        section("NEGOTIATION")
        print(f"  {result.negotiation.posture}")
        print(f"\n  Opening      {result.negotiation.opening_offer}")
        print(
            f"  Target       {result.negotiation.target_range_low}"
            f" – {result.negotiation.target_range_high}"
        )
        print(f"  Walk away    above {result.negotiation.walk_away_above}")

    section("FINAL ASSESSMENT — evidence, no verdict (spec §35)")
    for line in _wrap(narrative.report.final_assessment, 74):
        print(f"  {line}")

    section("LIMITATIONS")
    for limitation in result.limitations:
        for index, line in enumerate(_wrap(limitation, 72)):
            print(f"  {'•' if index == 0 else ' '} {line}")

    # --- the invariant -----------------------------------------------------
    from app.adapters.llm.validation import validate_report

    bundle = build_evidence_bundle(result)
    validation = validate_report(narrative.report, bundle)

    heading("  INVARIANT CHECKS")
    print(f"  Narrative produced by            : {narrative.generated_by} (language model off)")
    print(f"  Narrative passes anti-hallucination validation : {validation.ok}")
    print(f"  Every figure traceable to computed evidence    : {validation.ok}")
    print(f"  Report complete without the model             : "
          f"{bool(narrative.report.final_assessment and narrative.report.limitations)}")
    for finding in validation.findings:
        print(f"    FAILED: {finding}")

    print()
    return 0 if validation.ok else 1


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width=width) or [""]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
