"use client";

/**
 * "Why is this car cheaper?" as arithmetic (spec §19).
 *
 * Starts at the comparable median and walks through each measured factor to the
 * asking price. The final row — the unexplained remainder — is the one that
 * matters: it is the part of the discount that mileage, model year and disclosed
 * condition do not account for, and therefore the part a buyer has to go and
 * investigate.
 *
 * The rows sum exactly, because the backend's decomposition is an identity
 * rather than an approximation:
 *
 *     asking − median = (central − median) + (asking − central)
 */

import type { GapAnalysis } from "@/lib/types";
import { formatAzn, formatSignedAzn } from "@/lib/format";
import { Callout } from "./ui";

export function GapWaterfall({ gap }: { gap: GapAnalysis }) {
  const magnitudes = [
    Math.abs(gap.total_gap_azn),
    ...gap.components.map((component) => Math.abs(component.amount_azn)),
    Math.abs(gap.unexplained_azn),
  ];
  const scale = Math.max(...magnitudes, 1);

  const isDiscount = gap.unexplained_azn < 0;
  const unexplainedShare = 1 - gap.explained_share;

  return (
    <div>
      <dl className="space-y-0 text-sm">
        <Row
          label="Comparable listings median"
          value={formatAzn(gap.reference_median_azn)}
          emphasis
        />

        {gap.components.map((component) => (
          <Row
            key={component.factor}
            label={component.label}
            value={formatSignedAzn(component.amount_azn)}
            bar={{ width: (Math.abs(component.amount_azn) / scale) * 100, negative: component.amount_azn < 0 }}
            note={component.evidence}
          />
        ))}

        <Row
          label="Value adjusted for this vehicle"
          value={formatAzn(gap.reference_median_azn + gap.explained_azn)}
          emphasis
          divider
        />

        <Row
          label="Unexplained difference"
          value={formatSignedAzn(gap.unexplained_azn)}
          bar={{
            width: (Math.abs(gap.unexplained_azn) / scale) * 100,
            negative: gap.unexplained_azn < 0,
            unexplained: true,
          }}
          strong
        />

        <Row
          label="Asking price"
          value={formatAzn(gap.reference_median_azn + gap.total_gap_azn)}
          emphasis
          divider
        />
      </dl>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between text-xs text-ink-muted">
          <span>Explained by measurable factors</span>
          <span className="tnum">{Math.round(gap.explained_share * 100)}%</span>
        </div>
        <div className="flex h-2 overflow-hidden rounded-full bg-surface-sunken">
          <div
            className="bg-accent/70"
            style={{ width: `${Math.max(0, gap.explained_share * 100)}%` }}
          />
          <div
            className="bg-rating-high/60"
            style={{ width: `${Math.max(0, unexplainedShare * 100)}%` }}
          />
        </div>
      </div>

      {isDiscount && unexplainedShare >= 0.35 ? (
        <div className="mt-4">
          <Callout tone="warning" title="This is the part worth asking about">
            About{" "}
            <strong className="tnum font-semibold">
              {formatAzn(Math.abs(gap.unexplained_azn))}
            </strong>{" "}
            of the lower price is not accounted for by the differences we could
            measure. That does not mean something is wrong with the car — it means
            the reason has not been established, and establishing it is the next
            step.
          </Callout>
        </div>
      ) : null}
    </div>
  );
}

function Row({
  label,
  value,
  bar,
  note,
  emphasis = false,
  strong = false,
  divider = false,
}: {
  label: string;
  value: string;
  bar?: { width: number; negative: boolean; unexplained?: boolean };
  note?: string;
  emphasis?: boolean;
  strong?: boolean;
  divider?: boolean;
}) {
  return (
    <div className={divider ? "border-t border-surface-border pt-2.5" : ""}>
      <div className="flex items-baseline justify-between gap-4 py-1.5">
        <dt
          className={
            emphasis
              ? "font-medium text-ink"
              : strong
                ? "font-medium text-rating-high"
                : "text-ink-soft"
          }
        >
          {label}
        </dt>
        <dd
          className={`tnum shrink-0 ${
            emphasis ? "font-semibold text-ink" : strong ? "font-semibold text-rating-high" : "text-ink-soft"
          }`}
        >
          {value}
        </dd>
      </div>

      {bar ? (
        <div className="mb-1 h-1 w-full overflow-hidden rounded-full bg-surface-sunken">
          <div
            className={`h-full ${
              bar.unexplained
                ? "bg-rating-high/60"
                : bar.negative
                  ? "bg-ink-faint"
                  : "bg-accent/50"
            }`}
            style={{ width: `${Math.min(100, bar.width)}%` }}
          />
        </div>
      ) : null}

      {note ? <p className="pb-1 text-xs leading-relaxed text-ink-muted">{note}</p> : null}
    </div>
  );
}
