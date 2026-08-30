"use client";

import { useCallback, useEffect, useState } from "react";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, fetchDiscover } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import { useSession } from "@/lib/session";
import type { DiscoverResponse, Recommendation } from "@/lib/types";

/**
 * Discover: a budget, and what fits it.
 *
 * The budget is the only number in this product inferred about a *person*
 * rather than measured from the market, and the panel is built so that it
 * cannot be mistaken for the latter — it is labelled as an estimate, it says
 * how many observations it rests on, and the control to replace it sits next
 * to it rather than behind a menu.
 */
export function DiscoverPanel() {
  const { t, locale } = useLocale();
  const { user, loading: sessionLoading } = useSession();
  const [data, setData] = useState<DiscoverResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<{ low: string; high: string }>({ low: "", high: "" });
  const [applied, setApplied] = useState<{ low: number; high: number } | null>(null);

  const load = useCallback(
    async (override: { low: number; high: number } | null) => {
      setError(null);
      setData(null);
      try {
        setData(await fetchDiscover(override ?? undefined));
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          setError(null);
          setData(null);
          return;
        }
        setError(caught instanceof ApiError ? caught.message : String(caught));
      }
    },
    [],
  );

  useEffect(() => {
    if (sessionLoading) return;
    if (!user && !applied) return;
    void load(applied);
  }, [user, applied, sessionLoading, load]);

  const budget = data?.budget ?? null;
  const canApply = Number(range.low) > 0 && Number(range.high) >= Number(range.low);

  return (
    <div>
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.nav.discover}</h1>
      <p className="mt-2 text-sm text-ink-soft">{t.navHint.discover}</p>

      {/* Budget */}
      <Card className="mt-6">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <p className="stat-label">{t.discover.budgetTitle}</p>
          {budget ? (
            <span className="badge border-surface-border text-ink-muted">
              {budget.source === "stated" ? t.discover.stated : t.discover.inferred}
            </span>
          ) : null}
        </div>

        {budget ? (
          <>
            <p className="stat-value">
              {Number(budget.low_azn).toLocaleString(locale)} –{" "}
              {Number(budget.high_azn).toLocaleString(locale)}
              <span className="ml-1 text-sm font-normal text-ink-muted">AZN</span>
            </p>
            {budget.source === "history" ? (
              <p className="mt-1 text-xs text-ink-muted">
                {budget.observations} · {t.discover.basedOnObservations}
              </p>
            ) : null}
          </>
        ) : (
          <p className="mt-2 text-sm text-ink-soft">
            {!user && !applied
              ? t.discover.signInOrSet
              : t.discover.needMore.replace(
                  "{n}",
                  String(data?.observations_needed ?? 3),
                )}
          </p>
        )}

        {data?.note && budget?.source === "history" ? (
          <p className="mt-3 border-t border-surface-border pt-3 text-xs leading-relaxed text-ink-muted">
            {data.note}
          </p>
        ) : null}

        {/* Override */}
        <div className="mt-4 border-t border-surface-border pt-4">
          <p className="label">{t.discover.setRange}</p>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <input
              aria-label={t.discover.from}
              inputMode="numeric"
              className="field tnum max-w-[9rem]"
              placeholder="15000"
              value={range.low}
              onChange={(e) => setRange((r) => ({ ...r, low: e.target.value.replace(/\D/g, "") }))}
            />
            <span className="pb-2 text-ink-muted">–</span>
            <input
              aria-label={t.discover.to}
              inputMode="numeric"
              className="field tnum max-w-[9rem]"
              placeholder="25000"
              value={range.high}
              onChange={(e) => setRange((r) => ({ ...r, high: e.target.value.replace(/\D/g, "") }))}
            />
            <button
              type="button"
              disabled={!canApply}
              onClick={() => setApplied({ low: Number(range.low), high: Number(range.high) })}
              className="min-h-[44px] cursor-pointer rounded-md bg-accent px-4 text-sm font-medium
                         text-white transition-colors duration-200 hover:bg-accent/90
                         disabled:cursor-not-allowed disabled:opacity-60"
            >
              {t.discover.apply}
            </button>
            {applied ? (
              <button
                type="button"
                onClick={() => {
                  setApplied(null);
                  setRange({ low: "", high: "" });
                }}
                className="min-h-[44px] cursor-pointer px-2 text-sm text-accent hover:underline"
              >
                {t.discover.reset}
              </button>
            ) : null}
          </div>
        </div>
      </Card>

      {/* Recommendations */}
      <div className="mt-6">
        {error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : data === null && (user || applied) ? (
          <Card>
            <Spinner label="" />
          </Card>
        ) : data && budget && data.recommendations.length === 0 ? (
          <Card>
            <p className="text-sm text-ink-soft">{t.discover.noMatches}</p>
          </Card>
        ) : data ? (
          <ul className="grid gap-3">
            {data.recommendations.map((item) => (
              <RecommendationRow key={item.listing_id} item={item} />
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

function RecommendationRow({ item }: { item: Recommendation }) {
  const { t, locale } = useLocale();
  const price = Number(item.price_azn);
  const title = [item.model_year, item.make, item.model].filter(Boolean).join(" ") || "—";

  return (
    <li className="card flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-ink">{title}</p>
        <p className="mt-1 text-xs text-ink-muted">
          {item.city ?? "—"}
          {item.mileage_km != null ? ` · ${item.mileage_km.toLocaleString(locale)} km` : ""}
        </p>
        {item.vs_median_pct != null && item.vs_median_pct > 0 ? (
          <p className="mt-1 text-xs text-rating-great">
            {item.vs_median_pct.toFixed(1)}% {t.discover.atOrBelow}
          </p>
        ) : null}
      </div>

      <div className="flex items-center gap-3">
        <p className="tnum text-sm font-semibold text-ink">
          {price.toLocaleString(locale)}
          <span className="ml-1 font-normal text-ink-muted">AZN</span>
        </p>
        {item.source_url ? (
          <a
            href={item.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-[44px] cursor-pointer items-center rounded-md border
                       border-surface-border px-3 text-sm text-ink-soft transition-colors
                       duration-200 hover:bg-surface-sunken"
          >
            {t.actions.buy}
          </a>
        ) : null}
      </div>
    </li>
  );
}
