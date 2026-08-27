/**
 * Formatting and presentation mapping.
 *
 * The rating styles here never rely on colour alone. Every badge renders its
 * label and its percentage alongside the colour, because a colour-only signal
 * is invisible to roughly one in twelve male readers — and because "GOOD VALUE"
 * in green tells a buyer far less than "3.1% below the comparable median" does.
 */

import type { DealRating, Priority, RiskSeverity } from "./types";

export function formatAzn(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  return `${Math.round(amount).toLocaleString("en-US")} AZN`;
}

export function formatKm(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toLocaleString("en-US")} km`;
}

export function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

export function formatSignedAzn(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${Math.abs(Math.round(value)).toLocaleString("en-US")} AZN`;
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value)}%`;
}

interface RatingStyle {
  label: string;
  /** One line explaining what the category means, shown under the badge. */
  meaning: string;
  badge: string;
  bar: string;
  text: string;
}

const RATING_STYLES: Record<DealRating, RatingStyle> = {
  GREAT_VALUE: {
    label: "Great value",
    meaning: "Meaningfully below comparable listings, with no major risk indicators.",
    badge: "bg-rating-great/10 text-rating-great border-rating-great/30",
    bar: "bg-rating-great",
    text: "text-rating-great",
  },
  GOOD_VALUE: {
    label: "Good value",
    meaning: "Below or close to what comparable listings ask.",
    badge: "bg-rating-good/10 text-rating-good border-rating-good/30",
    bar: "bg-rating-good",
    text: "text-rating-good",
  },
  FAIR_VALUE: {
    label: "Fair value",
    meaning: "In line with comparable listings.",
    badge: "bg-rating-fair/10 text-rating-fair border-rating-fair/30",
    bar: "bg-rating-fair",
    text: "text-rating-fair",
  },
  HIGH_PRICED: {
    label: "High priced",
    meaning: "Above what comparable listings ask.",
    badge: "bg-rating-high/10 text-rating-high border-rating-high/30",
    bar: "bg-rating-high",
    text: "text-rating-high",
  },
  OVERPRICED: {
    label: "Overpriced",
    meaning: "Significantly above comparable listings.",
    badge: "bg-rating-over/10 text-rating-over border-rating-over/30",
    bar: "bg-rating-over",
    text: "text-rating-over",
  },
  SUSPICIOUSLY_CHEAP: {
    label: "Suspiciously cheap",
    meaning:
      "Far below comparable listings, and the difference is not explained by mileage, age or disclosed condition. This is a prompt to investigate, not a conclusion about the car.",
    badge: "bg-rating-suspect/10 text-rating-suspect border-rating-suspect/30",
    bar: "bg-rating-suspect",
    text: "text-rating-suspect",
  },
  INSUFFICIENT_DATA: {
    label: "Not enough data",
    meaning: "Too few comparable listings to place this price against the market.",
    badge: "bg-ink-muted/10 text-ink-muted border-ink-muted/30",
    bar: "bg-ink-faint",
    text: "text-ink-muted",
  },
};

export function ratingStyle(rating: DealRating): RatingStyle {
  return RATING_STYLES[rating] ?? RATING_STYLES.INSUFFICIENT_DATA;
}

const SEVERITY_STYLES: Record<RiskSeverity, { label: string; className: string }> = {
  INFO: { label: "Note", className: "bg-ink-muted/10 text-ink-muted border-ink-muted/25" },
  LOW: { label: "Low", className: "bg-risk-low/10 text-risk-low border-risk-low/25" },
  MODERATE: {
    label: "Moderate",
    className: "bg-risk-moderate/10 text-risk-moderate border-risk-moderate/25",
  },
  HIGH: { label: "High", className: "bg-risk-high/10 text-risk-high border-risk-high/25" },
  CRITICAL: {
    label: "Critical",
    className: "bg-risk-critical/10 text-risk-critical border-risk-critical/25",
  },
};

export function severityStyle(severity: RiskSeverity) {
  return SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.INFO;
}

export function riskBandColor(score: number): string {
  if (score <= 20) return "bg-risk-low";
  if (score <= 40) return "bg-risk-moderate/70";
  if (score <= 60) return "bg-risk-moderate";
  if (score <= 80) return "bg-risk-high";
  return "bg-risk-critical";
}

const PRIORITY_STYLES: Record<Priority, { label: string; className: string }> = {
  high: { label: "High", className: "bg-risk-high/10 text-risk-high border-risk-high/25" },
  medium: {
    label: "Medium",
    className: "bg-risk-moderate/10 text-risk-moderate border-risk-moderate/25",
  },
  low: { label: "Low", className: "bg-ink-muted/10 text-ink-muted border-ink-muted/25" },
};

export function priorityStyle(priority: Priority) {
  return PRIORITY_STYLES[priority] ?? PRIORITY_STYLES.low;
}

/**
 * How a claim's epistemic status is shown.
 *
 * Rendering these three identically would defeat the purpose of tagging them:
 * a possibility presented with the visual weight of a fact is exactly the
 * failure the product exists to prevent.
 */
export function claimStyle(kind: "FACT" | "INFERENCE" | "POSSIBILITY") {
  switch (kind) {
    case "FACT":
      return { label: "Fact", className: "text-ink border-l-2 border-accent pl-3" };
    case "INFERENCE":
      return {
        label: "Interpretation",
        className: "text-ink-soft border-l-2 border-ink-faint pl-3",
      };
    default:
      return {
        label: "Needs verification",
        className:
          "text-ink-soft border-l-2 border-dashed border-rating-high/60 pl-3 italic",
      };
  }
}

/** Position of a value within a distribution, clamped to a drawable range. */
export function positionInRange(value: number, low: number, high: number): number {
  if (high <= low) return 50;
  return Math.max(0, Math.min(100, ((value - low) / (high - low)) * 100));
}
