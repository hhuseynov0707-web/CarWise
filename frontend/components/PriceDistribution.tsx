"use client";

/**
 * Where the asking price sits in the market (spec §16).
 *
 * The chart plots the actual percentile bands of comparable asking prices —
 * p10 to p90, with the interquartile box — and marks the asking price and the
 * fair range on the same axis. That makes the claim checkable: a reader can see
 * that "78th percentile" corresponds to a marker three-quarters of the way
 * along the observed spread, rather than taking the number on trust.
 *
 * Everything shown here is a computed statistic passed in as props. The
 * component does no arithmetic beyond mapping values onto the axis.
 */

import type { Distribution, Money } from "@/lib/types";
import { formatAzn, positionInRange } from "@/lib/format";

interface Props {
  distribution: Distribution;
  askingPrice: Money | null;
  fairLow: Money | null;
  fairHigh: Money | null;
  percentile: number | null;
}

export function PriceDistribution({
  distribution,
  askingPrice,
  fairLow,
  fairHigh,
  percentile,
}: Props) {
  // Pad the axis so markers at the extremes are not clipped.
  const values = [
    distribution.p10,
    distribution.p90,
    askingPrice?.amount ?? distribution.p50,
    fairLow?.amount ?? distribution.p25,
    fairHigh?.amount ?? distribution.p75,
  ];
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const pad = (rawMax - rawMin) * 0.08 || rawMax * 0.05;
  const min = rawMin - pad;
  const max = rawMax + pad;

  const at = (value: number) => positionInRange(value, min, max);

  const boxLeft = at(distribution.p25);
  const boxRight = at(distribution.p75);
  const whiskerLeft = at(distribution.p10);
  const whiskerRight = at(distribution.p90);
  const medianAt = at(distribution.p50);

  const askingAt = askingPrice ? at(askingPrice.amount) : null;
  const fairLeft = fairLow ? at(fairLow.amount) : null;
  const fairRight = fairHigh ? at(fairHigh.amount) : null;

  return (
    <figure className="mt-2">
      <figcaption className="sr-only">
        Distribution of asking prices across {distribution.sample_size} comparable
        listings, from {formatAzn(distribution.p10)} at the 10th percentile to{" "}
        {formatAzn(distribution.p90)} at the 90th, with a median of{" "}
        {formatAzn(distribution.p50)}.
        {askingPrice
          ? ` This vehicle asks ${formatAzn(askingPrice.amount)}${
              percentile !== null
                ? `, at approximately the ${Math.round(percentile)}th percentile.`
                : "."
            }`
          : ""}
      </figcaption>

      <div className="relative h-28 select-none" aria-hidden>
        {/* Fair market range band */}
        {fairLeft !== null && fairRight !== null ? (
          <div
            className="absolute top-8 h-10 rounded-sm border-x border-dashed
                       border-accent/40 bg-accent/[0.07]"
            style={{ left: `${fairLeft}%`, width: `${Math.max(0.5, fairRight - fairLeft)}%` }}
          />
        ) : null}

        {/* p10–p90 whisker */}
        <div
          className="absolute top-[3.25rem] h-px bg-ink-faint"
          style={{ left: `${whiskerLeft}%`, width: `${whiskerRight - whiskerLeft}%` }}
        />
        <div
          className="absolute top-11 h-5 w-px bg-ink-faint"
          style={{ left: `${whiskerLeft}%` }}
        />
        <div
          className="absolute top-11 h-5 w-px bg-ink-faint"
          style={{ left: `${whiskerRight}%` }}
        />

        {/* Interquartile box */}
        <div
          className="absolute top-[2.75rem] h-6 rounded-sm border border-ink-faint/70
                     bg-surface-sunken"
          style={{ left: `${boxLeft}%`, width: `${Math.max(0.5, boxRight - boxLeft)}%` }}
        />

        {/* Median */}
        <div
          className="absolute top-[2.75rem] h-6 w-0.5 bg-ink-soft"
          style={{ left: `${medianAt}%` }}
        />
        <div
          className="absolute top-[5.25rem] -translate-x-1/2 whitespace-nowrap text-[10px]
                     text-ink-muted"
          style={{ left: `${medianAt}%` }}
        >
          median {formatAzn(distribution.p50)}
        </div>

        {/* Asking price marker */}
        {askingAt !== null && askingPrice ? (
          <>
            <div
              className="absolute top-6 h-14 w-0.5 bg-ink"
              style={{ left: `${askingAt}%` }}
            />
            <div
              className="absolute top-0 -translate-x-1/2 whitespace-nowrap rounded
                         bg-ink px-1.5 py-0.5 text-[10px] font-medium text-white"
              style={{ left: `${clampLabel(askingAt)}%` }}
            >
              asking {formatAzn(askingPrice.amount)}
            </div>
          </>
        ) : null}
      </div>

      <div className="mt-1 flex justify-between text-[10px] text-ink-faint tnum">
        <span>{formatAzn(distribution.p10)}</span>
        <span>10th – 90th percentile of {distribution.sample_size} listings</span>
        <span>{formatAzn(distribution.p90)}</span>
      </div>

      {percentile !== null && askingPrice ? (
        <p className="mt-3 text-sm text-ink-soft">
          This asking price sits at approximately the{" "}
          <strong className="font-semibold text-ink tnum">
            {Math.round(percentile)}
            {ordinal(Math.round(percentile))}
          </strong>{" "}
          percentile — about {Math.round(percentile)}% of comparable listings ask less.
        </p>
      ) : null}
    </figure>
  );
}

/** Keep an edge label from overflowing the plot area. */
function clampLabel(position: number): number {
  return Math.max(8, Math.min(92, position));
}

function ordinal(value: number): string {
  const remainder = value % 100;
  if (remainder >= 11 && remainder <= 13) return "th";
  switch (value % 10) {
    case 1:
      return "st";
    case 2:
      return "nd";
    case 3:
      return "rd";
    default:
      return "th";
  }
}
