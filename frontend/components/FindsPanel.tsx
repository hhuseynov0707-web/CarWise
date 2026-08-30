"use client";

import { useEffect, useState } from "react";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, fetchFinds } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import type { Find, FindsResponse } from "@/lib/types";

/**
 * Today's finds.
 *
 * Each row leads with the gap and then, immediately, with what explains it —
 * the sample it is measured against and how the mileage compares. Mileage
 * sits next to the percentage rather than below the fold because it is the
 * ordinary reason a car is cheap, and a reader who never scrolls should still
 * have met it.
 */
export function FindsPanel() {
  const { t } = useLocale();
  const [data, setData] = useState<FindsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchFinds()
      .then((result) => !cancelled && setData(result))
      .catch((caught) =>
        !cancelled && setError(caught instanceof ApiError ? caught.message : String(caught)),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.nav.deals}</h1>
      <p className="mt-2 text-sm text-ink-soft">{t.navHint.deals}</p>

      {data ? (
        <div className="mt-4">
          <Callout tone="info" title="">
            {data.caveat}
          </Callout>
        </div>
      ) : null}

      <div className="mt-6">
        {error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : data === null ? (
          <Card>
            <Spinner label="" />
          </Card>
        ) : data.finds.length === 0 ? (
          <Card>
            <p className="text-sm font-medium text-ink">{t.finds.empty}</p>
            <p className="mt-1 text-sm text-ink-soft">{t.finds.emptyHint}</p>
          </Card>
        ) : (
          <ul className="grid gap-3">
            {data.finds.map((find) => (
              <FindRow key={find.listing_id} find={find} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FindRow({ find }: { find: Find }) {
  const { t, locale } = useLocale();
  const price = Number(find.price_azn);
  const median = Number(find.median_azn);
  const mileageGap = find.mileage_vs_median_pct;

  const title = [find.model_year, find.make, find.model].filter(Boolean).join(" ") || "—";

  // A gap the mileage already explains is not the same finding as one it does
  // not, and the row says which it is rather than leaving both looking alike.
  const mileageNote =
    mileageGap === null
      ? t.finds.mileageUnknown
      : `${Math.abs(Math.round(mileageGap))}% ${
          mileageGap > 0 ? t.finds.mileageAbove : t.finds.mileageBelow
        }`;

  return (
    <li className="card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-ink">{title}</p>
          <p className="mt-1 text-xs text-ink-muted">
            {find.city ?? "—"}
            {find.mileage_km != null ? ` · ${find.mileage_km.toLocaleString(locale)} km` : ""}
          </p>
        </div>
        <div className="text-right">
          <p className="tnum text-figure-lg font-semibold leading-none text-ink">
            {price.toLocaleString(locale)}
            <span className="ml-1 text-sm font-normal text-ink-muted">AZN</span>
          </p>
          <p className="mt-1 text-xs font-medium text-rating-great">
            {find.below_median_pct.toFixed(1)}% {t.finds.belowMedian}
          </p>
        </div>
      </div>

      <dl className="mt-3 grid gap-1 border-t border-surface-border pt-3 text-xs text-ink-soft">
        <div className="flex justify-between gap-4">
          <dt>{t.finds.basedOn}</dt>
          <dd className="tnum">
            {find.sample_size} {t.finds.listings} · {median.toLocaleString(locale)} AZN
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt>km</dt>
          {/* Amber when the mileage is above the median: that is the gap
              explaining itself, and it should not read like good news. */}
          <dd className={mileageGap != null && mileageGap > 0 ? "text-rating-high" : undefined}>
            {mileageNote}
          </dd>
        </div>
      </dl>

      {find.source_url ? (
        <div className="mt-3 flex justify-end">
          <a
            href={find.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-[44px] cursor-pointer items-center rounded-md border
                       border-surface-border px-4 text-sm text-ink-soft transition-colors
                       duration-200 hover:bg-surface-sunken"
          >
            {t.actions.buy}
          </a>
        </div>
      ) : null}
    </li>
  );
}
