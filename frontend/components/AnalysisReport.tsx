"use client";

/**
 * The analysis report (spec §34, §54, §55).
 *
 * Ordered to answer, in the first screen and without scrolling: what car is
 * this, what is it worth, what is being asked, is that above or below the
 * market, what are the risks, and how confident is the analysis. Everything
 * after that is the evidence behind those answers.
 *
 * Two rules hold throughout:
 *
 * - No headline figure appears without its reasoning reachable beside it.
 * - Nothing here computes anything. Every number is rendered as received.
 */

import type { Analysis } from "@/lib/types";
import {
  claimStyle,
  formatAzn,
  formatKm,
  formatPercent,
  formatSignedPct,
  priorityStyle,
  ratingStyle,
  riskBandColor,
  severityStyle,
} from "@/lib/format";
import { Badge, Callout, Card, EvidenceList, SectionHeading, Stat, Why } from "./ui";
import { GapWaterfall } from "./GapWaterfall";
import { PriceDistribution } from "./PriceDistribution";

export function AnalysisReport({ analysis }: { analysis: Analysis }) {
  const { valuation, price_position: position, risk, confidence, market } = analysis;
  const rating = ratingStyle(position.rating);
  const hasValuation = valuation.outcome === "OK";

  return (
    <div className="space-y-4">
      <Headline analysis={analysis} />

      {!hasValuation ? (
        <InsufficientData analysis={analysis} />
      ) : (
        <>
          {/* ---- The six questions, answered above the fold ---- */}
          <Card>
            <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
              <Stat
                label="Fair market range"
                value={
                  <span className="text-figure-lg">
                    {formatAzn(valuation.fair_market_low?.amount)}
                    <span className="mx-1 text-ink-faint">–</span>
                    {formatAzn(valuation.fair_market_high?.amount)}
                  </span>
                }
                sub={`Central estimate ${formatAzn(valuation.central_estimate?.amount)}`}
              />
              <Stat
                label="Asking price"
                value={formatAzn(position.asking_price?.amount)}
                sub={
                  position.difference_pct !== null
                    ? `${formatSignedPct(position.difference_pct)} vs central estimate`
                    : undefined
                }
              />
              <div>
                <div className="stat-label">Market position</div>
                <div className={`mt-1 text-figure-lg font-semibold ${rating.text}`}>
                  {position.rating_label}
                </div>
                <div className="mt-1 text-xs text-ink-muted">{rating.meaning}</div>
              </div>
              <div>
                <div className="stat-label">Risk indicators</div>
                <div className="mt-1 flex items-baseline gap-2">
                  <span className="text-figure-lg font-semibold tnum text-ink">
                    {risk.score}
                  </span>
                  <span className="text-sm text-ink-muted">/ 100</span>
                </div>
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
                  <div
                    className={`h-full ${riskBandColor(risk.score)}`}
                    style={{ width: `${risk.score}%` }}
                  />
                </div>
                <div className="mt-1.5 text-xs text-ink-muted">{risk.band_label}</div>
              </div>
            </div>

            <div className="mt-5 border-t border-surface-border pt-4">
              <Why label="Why this rating?" count={position.rationale.length} defaultOpen>
                <EvidenceList items={position.rationale} />
              </Why>
            </div>

            <PriceBasisNote analysis={analysis} />
          </Card>

          {/* ---- Distribution ---- */}
          {market.asking_price_distribution ? (
            <Card>
              <SectionHeading
                title="Where this price sits in the market"
                hint={`${market.comparable_count} comparable listings · ${market.match_level}`}
              />
              <PriceDistribution
                distribution={market.asking_price_distribution}
                askingPrice={position.asking_price}
                fairLow={valuation.fair_market_low}
                fairHigh={valuation.fair_market_high}
                percentile={position.percentile}
              />
            </Card>
          ) : null}

          {/* ---- Gap analysis ---- */}
          {position.gap_analysis && position.gap_analysis.components.length ? (
            <Card>
              <SectionHeading
                title="Where the price difference comes from"
                hint="Each factor priced from the comparable listings themselves"
              />
              <GapWaterfall gap={position.gap_analysis} />
              {analysis.candidate_explanations.length ? (
                <Why
                  label="Possible explanations for the remainder"
                  count={analysis.candidate_explanations.length}
                >
                  <EvidenceList items={analysis.candidate_explanations} />
                </Why>
              ) : null}
            </Card>
          ) : null}
        </>
      )}

      <RiskSection analysis={analysis} />
      <ConfidenceSection analysis={analysis} />
      {hasValuation ? <ValuationBasis analysis={analysis} /> : null}
      <ComparablesSection analysis={analysis} />
      <ActionSection analysis={analysis} />
      <NegotiationSection analysis={analysis} />
      <NarrativeSection analysis={analysis} />
      <LimitationsSection analysis={analysis} />
    </div>
  );
}

/* ------------------------------------------------------------------ */

function Headline({ analysis }: { analysis: Analysis }) {
  const { vehicle, confidence } = analysis;
  return (
    <Card className="bg-ink text-white">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-figure-lg font-semibold">{vehicle.description}</h1>
          <p className="mt-1.5 text-sm text-white/60">
            {[
              vehicle.mileage_km !== null ? formatKm(vehicle.mileage_km) : "mileage not stated",
              vehicle.city ?? "location not stated",
              vehicle.vin_provided ? "VIN provided" : "no VIN",
            ].join(" · ")}
          </p>
        </div>
        <div className="text-right">
          <div className="text-xs uppercase tracking-wide text-white/50">Confidence</div>
          <div className="text-figure-lg font-semibold tnum">
            {confidence.score_percent}%
          </div>
          <div className="text-xs text-white/50">{confidence.band.toLowerCase()}</div>
        </div>
      </div>
      {vehicle.unknown_attributes.length ? (
        <p className="mt-4 border-t border-white/10 pt-3 text-xs text-white/60">
          Not specified: {vehicle.unknown_attributes.join(", ")}. Adding these narrows
          the comparable set and raises confidence.
        </p>
      ) : null}
    </Card>
  );
}

function PriceBasisNote({ analysis }: { analysis: Analysis }) {
  if (analysis.valuation.price_basis !== "ASKING") return null;
  return (
    <p className="mt-4 rounded-md bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
      These figures are drawn from <strong>asking prices</strong>, not confirmed sale
      prices. Cars in this market frequently sell for less than they are listed at, so
      the price actually paid is likely to sit below this range.
    </p>
  );
}

function InsufficientData({ analysis }: { analysis: Analysis }) {
  const { valuation, confidence } = analysis;
  return (
    <Card>
      <SectionHeading title="Not enough comparable data to value this vehicle" />
      <Callout tone="info">{valuation.insufficient_reason}</Callout>
      {confidence.improvements.length ? (
        <div className="mt-4">
          <p className="section-title mb-2">What would help</p>
          <EvidenceList items={confidence.improvements} />
        </div>
      ) : null}
      <p className="mt-4 text-sm text-ink-muted">
        The vehicle details, risk indicators and inspection guidance below are still
        based on what we could establish.
      </p>
    </Card>
  );
}

function RiskSection({ analysis }: { analysis: Analysis }) {
  const { risk } = analysis;
  if (!risk.signals.length && !risk.positives.length) return null;

  return (
    <Card>
      <SectionHeading
        title="Risk indicators"
        hint="Strength and number of indicators found in the available data — not a probability that the car is bad"
      />

      {risk.positives.length ? (
        <div className="mb-5 space-y-2">
          {risk.positives.map((positive, index) => (
            <div key={index} className="flex gap-2.5 text-sm">
              <span aria-hidden className="mt-0.5 text-risk-low">
                ✓
              </span>
              <div>
                <span className="font-medium text-ink">{positive.title}</span>
                <span className="text-ink-soft"> — {positive.evidence.join(" ")}</span>
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="space-y-4">
        {risk.signals.map((signal, index) => {
          const style = severityStyle(signal.severity);
          return (
            <article
              key={index}
              className="rounded-md border border-surface-border p-4"
            >
              <header className="mb-2 flex flex-wrap items-center gap-2">
                <Badge className={style.className}>{style.label}</Badge>
                <h3 className="text-sm font-medium text-ink">{signal.title}</h3>
              </header>

              <EvidenceList items={signal.evidence} />

              <p className="mt-3 text-sm leading-relaxed text-ink-soft">
                {signal.interpretation}
              </p>

              <p className="mt-3 rounded bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
                <span className="font-medium text-ink">How to check: </span>
                {signal.recommended_verification}
              </p>
            </article>
          );
        })}
      </div>

      {risk.contributions.length ? (
        <Why label="How the risk score is made up" count={risk.contributions.length}>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-ink-muted">
                <th className="pb-2 font-medium">Indicator</th>
                <th className="pb-2 text-right font-medium">Contribution</th>
              </tr>
            </thead>
            <tbody>
              {risk.contributions.map((contribution, index) => (
                <tr key={index} className="border-t border-surface-border">
                  <td className="py-2 pr-4 text-ink-soft">{contribution.title}</td>
                  <td className="py-2 text-right tnum text-ink">
                    {contribution.marginal_points.toFixed(1)} pts
                  </td>
                </tr>
              ))}
              <tr className="border-t border-surface-border font-medium">
                <td className="py-2 text-ink">Total</td>
                <td className="py-2 text-right tnum text-ink">{risk.score} / 100</td>
              </tr>
            </tbody>
          </table>
          <p className="mt-3 text-xs text-ink-muted">
            Indicators combine so that each additional one adds less than the last —
            three moderate findings do not equal one severe finding, and no number of
            minor ones reaches 100.
          </p>
        </Why>
      ) : null}
    </Card>
  );
}

function ConfidenceSection({ analysis }: { analysis: Analysis }) {
  const { confidence } = analysis;
  if (!confidence.components.length) return null;

  return (
    <Card>
      <SectionHeading
        title={`Confidence: ${confidence.score_percent}%`}
        hint="Every point accounted for"
        right={
          <Badge className="border-ink-muted/25 bg-ink-muted/10 text-ink-muted">
            {confidence.band.toLowerCase()}
          </Badge>
        }
      />

      <div className="space-y-2.5">
        {confidence.components.map((component) => (
          <div key={component.name}>
            <div className="flex items-baseline justify-between gap-4 text-sm">
              <span className="text-ink-soft">{component.label}</span>
              <span className="shrink-0 tnum text-ink">
                {component.contribution_points.toFixed(1)} pts
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-sunken">
              <div
                className="h-full bg-accent/60"
                style={{ width: `${Math.max(0, Math.min(100, component.score * 100))}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-ink-muted">{component.explanation}</p>
          </div>
        ))}
      </div>

      {!confidence.calibrated ? (
        <p className="mt-4 rounded-md bg-surface-sunken px-3 py-2 text-xs text-ink-muted">
          This figure reflects how much evidence the estimate rests on — sample size,
          similarity, freshness and completeness. It has not yet been calibrated
          against verified outcomes, so it is not a probability that the estimate is
          correct.
        </p>
      ) : null}

      {confidence.improvements.length ? (
        <Why label="How to improve this analysis" count={confidence.improvements.length}>
          <EvidenceList items={confidence.improvements} />
        </Why>
      ) : null}
    </Card>
  );
}

function ValuationBasis({ analysis }: { analysis: Analysis }) {
  const { valuation } = analysis;
  const applied = valuation.adjustments.filter((a) => a.applied);
  const unavailable = valuation.adjustments.filter((a) => !a.applied);

  return (
    <Card>
      <SectionHeading
        title="How the estimate was calculated"
        hint={`Starting from the median of ${valuation.comparable_count} comparable listings`}
      />

      <dl className="space-y-3 text-sm">
        <div className="flex items-baseline justify-between gap-4 border-b border-surface-border pb-2">
          <dt className="text-ink-soft">Comparable listings median</dt>
          <dd className="tnum font-medium text-ink">
            {formatAzn(valuation.raw_market_median?.amount)}
          </dd>
        </div>

        {applied.map((adjustment) => (
          <div key={adjustment.factor}>
            <div className="flex items-baseline justify-between gap-4">
              <dt className="text-ink-soft">{adjustment.label}</dt>
              <dd className="tnum text-ink">
                {adjustment.amount_azn > 0 ? "+" : "−"}
                {Math.abs(Math.round(adjustment.amount_azn)).toLocaleString("en-US")} AZN
              </dd>
            </div>
            <p className="mt-0.5 text-xs text-ink-muted">
              {adjustment.explanation}
              {adjustment.data_points ? ` (${adjustment.data_points} listings)` : ""}
            </p>
          </div>
        ))}

        <div className="flex items-baseline justify-between gap-4 border-t border-surface-border pt-2 font-medium">
          <dt className="text-ink">Central estimate</dt>
          <dd className="tnum text-ink">{formatAzn(valuation.central_estimate?.amount)}</dd>
        </div>
      </dl>

      {unavailable.length ? (
        <Why label="Factors we could not measure" count={unavailable.length}>
          <ul className="space-y-2">
            {unavailable.map((adjustment) => (
              <li key={adjustment.factor} className="text-sm">
                <span className="font-medium text-ink">{adjustment.label}: </span>
                <span className="text-ink-soft">{adjustment.explanation}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-ink-muted">
            These contribute nothing to the estimate. Each is listed with the reason it
            could not be measured rather than being filled in with an assumed value.
          </p>
        </Why>
      ) : null}

      {valuation.notes.length ? (
        <div className="mt-4 space-y-2">
          {valuation.notes.map((note, index) => (
            <p key={index} className="text-xs text-ink-muted">
              {note}
            </p>
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function ComparablesSection({ analysis }: { analysis: Analysis }) {
  const { comparables, market } = analysis;
  if (!comparables.length) return null;

  return (
    <Card>
      <SectionHeading
        title="Comparable vehicles"
        hint={`${market.comparable_count} analysed · average similarity ${formatPercent(
          market.mean_similarity * 100,
        )} · ${market.match_level}`}
      />

      {market.search_widened ? (
        <div className="mb-4">
          <Callout tone="info">
            The comparable search had to be widened beyond exactly-matching
            configurations to reach a usable sample. The comparison is therefore less
            exact than an identical match would be.
          </Callout>
        </div>
      ) : null}

      <div className="-mx-5 overflow-x-auto px-5 sm:mx-0 sm:px-0">
        <table className="w-full min-w-[36rem] text-sm">
          <thead>
            <tr className="border-b border-surface-border text-left text-xs uppercase tracking-wide text-ink-muted">
              <th className="pb-2 font-medium">Price</th>
              <th className="pb-2 font-medium">Year</th>
              <th className="pb-2 font-medium">Mileage</th>
              <th className="pb-2 font-medium">Location</th>
              <th className="pb-2 text-right font-medium">Similarity</th>
              <th className="pb-2 pl-4 font-medium">Differences</th>
            </tr>
          </thead>
          <tbody>
            {comparables.map((comparable) => (
              <tr key={comparable.listing_id} className="border-b border-surface-border/60">
                <td className="py-2 tnum font-medium text-ink">
                  {formatAzn(comparable.price.amount)}
                </td>
                <td className="py-2 tnum text-ink-soft">{comparable.model_year ?? "—"}</td>
                <td className="py-2 tnum text-ink-soft">{formatKm(comparable.mileage_km)}</td>
                <td className="py-2 text-ink-soft">{comparable.city ?? "—"}</td>
                <td className="py-2 text-right tnum text-ink-soft">
                  {Math.round(comparable.similarity * 100)}%
                </td>
                <td className="py-2 pl-4 text-xs text-ink-muted">
                  {comparable.differences.length
                    ? comparable.differences.join(", ")
                    : "closest match"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ActionSection({ analysis }: { analysis: Analysis }) {
  const questions = analysis.seller_questions;
  const items = analysis.inspection_priorities;
  if (!questions.length && !items.length) return null;

  const byPriority = (priority: string) => items.filter((item) => item.priority === priority);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {questions.length ? (
        <Card>
          <SectionHeading
            title="What to ask the seller"
            hint="Generated from the findings above, not a generic list"
          />
          <ol className="space-y-3">
            {questions.map((question, index) => {
              const style = priorityStyle(question.priority);
              return (
                <li key={index} className="border-l-2 border-surface-border pl-3">
                  <div className="mb-1 flex items-start gap-2">
                    <Badge className={style.className}>{style.label}</Badge>
                  </div>
                  <p className="text-sm font-medium text-ink">{question.question}</p>
                  <p className="mt-0.5 text-xs text-ink-muted">{question.why}</p>
                </li>
              );
            })}
          </ol>
        </Card>
      ) : null}

      {items.length ? (
        <Card>
          <SectionHeading
            title="What to inspect"
            hint="Prioritised by what this specific analysis found"
          />
          {(["high", "medium", "low"] as const).map((priority) => {
            const group = byPriority(priority);
            if (!group.length) return null;
            const style = priorityStyle(priority);
            return (
              <div key={priority} className="mb-4 last:mb-0">
                <Badge className={`${style.className} mb-2`}>{style.label} priority</Badge>
                <ul className="space-y-2">
                  {group.map((item, index) => (
                    <li key={index} className="text-sm">
                      <span className="text-ink">{item.item}</span>
                      <span className="block text-xs text-ink-muted">
                        {item.triggered_by === "standard due diligence"
                          ? item.reason
                          : `Triggered by: ${item.triggered_by}`}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </Card>
      ) : null}
    </div>
  );
}

function NegotiationSection({ analysis }: { analysis: Analysis }) {
  const { negotiation } = analysis;
  if (!negotiation.available) {
    return negotiation.unavailable_reason ? (
      <Card>
        <SectionHeading title="Negotiation" />
        <Callout tone="info">{negotiation.unavailable_reason}</Callout>
      </Card>
    ) : null;
  }

  return (
    <Card>
      <SectionHeading
        title="Negotiation"
        hint="Every figure anchored to the computed market range"
      />
      <p className="mb-4 text-sm text-ink-soft">{negotiation.posture}</p>

      <div className="mb-5 grid gap-4 sm:grid-cols-3">
        <Stat label="Opening position" value={formatAzn(negotiation.opening_offer?.amount)} />
        <Stat
          label="Target range"
          value={
            <span className="text-figure-lg">
              {formatAzn(negotiation.target_low?.amount)}
              <span className="mx-1 text-ink-faint">–</span>
              {formatAzn(negotiation.target_high?.amount)}
            </span>
          }
        />
        <Stat
          label="Stops being competitive above"
          value={formatAzn(negotiation.walk_away_above?.amount)}
        />
      </div>

      {negotiation.observed_market_reduction_pct !== null ? (
        <p className="mb-4 rounded-md bg-surface-sunken px-3 py-2 text-sm text-ink-soft">
          Comparable listings that changed price moved by a median of{" "}
          <strong className="tnum">
            {formatSignedPct(negotiation.observed_market_reduction_pct)}
          </strong>{" "}
          ({negotiation.reduction_sample_size} listings) — that is the room sellers in
          this segment have actually been giving.
        </p>
      ) : null}

      {negotiation.leverage.length ? (
        <div>
          <p className="section-title mb-2">What supports a lower price</p>
          <ul className="space-y-2.5">
            {negotiation.leverage.map((point, index) => (
              <li key={index} className="text-sm">
                <span className="font-medium text-ink">{point.title}</span>
                <span className="ml-2 text-xs uppercase tracking-wide text-ink-faint">
                  {point.strength}
                </span>
                <p className="mt-0.5 text-ink-soft">{point.evidence}</p>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Why label="How these figures were derived" count={negotiation.rationale.length}>
        <EvidenceList items={negotiation.rationale} />
      </Why>
    </Card>
  );
}

function NarrativeSection({ analysis }: { analysis: Analysis }) {
  const narrative = analysis.narrative;
  if (!narrative) return null;

  return (
    <Card>
      <SectionHeading
        title="Assessment"
        right={
          <Badge
            className={
              narrative.is_ai_generated
                ? "border-accent/25 bg-accent/10 text-accent"
                : "border-ink-muted/25 bg-ink-muted/10 text-ink-muted"
            }
          >
            {narrative.is_ai_generated ? "AI-written summary" : "Generated from the data"}
          </Badge>
        }
      />

      {narrative.degraded_reason ? (
        <div className="mb-4">
          <Callout>{narrative.degraded_reason}</Callout>
        </div>
      ) : null}

      <div className="prose-report">
        {narrative.price_explanation ? <p>{narrative.price_explanation}</p> : null}
        <p className="font-medium text-ink">{narrative.final_assessment}</p>
      </div>

      {narrative.risk_signals.length ? (
        <Why label="Statement-by-statement" count={narrative.risk_signals.length}>
          <ul className="space-y-2.5">
            {narrative.risk_signals.map((claim, index) => {
              const style = claimStyle(claim.kind);
              return (
                <li key={index} className={`text-sm ${style.className}`}>
                  <span className="mr-2 text-[10px] uppercase tracking-wide text-ink-faint">
                    {style.label}
                  </span>
                  {claim.statement}
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-ink-muted">
            Statements are separated into what the evidence establishes, what it
            suggests, and what remains to be verified.
          </p>
        </Why>
      ) : null}
    </Card>
  );
}

function LimitationsSection({ analysis }: { analysis: Analysis }) {
  return (
    <Card>
      <SectionHeading title="What this analysis cannot tell you" />
      <EvidenceList items={analysis.limitations} />
      <p className="mt-5 border-t border-surface-border pt-4 text-xs leading-relaxed text-ink-muted">
        {analysis.disclaimer}
      </p>
      <p className="mt-3 text-xs text-ink-faint tnum">
        Analysis {analysis.analysis_id} · generated{" "}
        {new Date(analysis.generated_at).toLocaleString("en-GB", {
          dateStyle: "long",
          timeStyle: "short",
        })}
        . Market data changes; this reflects the market as observed at that moment.
      </p>
    </Card>
  );
}
