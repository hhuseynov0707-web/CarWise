"use client";

import { useCallback, useEffect, useState } from "react";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, listSaved, removeSaved, saveVehicle } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import { useSession } from "@/lib/session";
import type { Analysis, SavedVehicle } from "@/lib/types";

/** The Saved tab. */
export function SavedPanel() {
  const { t } = useLocale();
  const { user, loading: sessionLoading } = useSession();
  const [rows, setRows] = useState<SavedVehicle[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await listSaved());
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : String(caught));
    }
  }, []);

  useEffect(() => {
    if (user) void load();
    else setRows(null);
  }, [user, load]);

  if (sessionLoading) {
    return (
      <Card>
        <Spinner label="" />
      </Card>
    );
  }

  return (
    <div>
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.nav.saved}</h1>
      <p className="mt-2 text-sm text-ink-soft">{t.navHint.saved}</p>

      <div className="mt-6">
        {!user ? (
          <Callout tone="info" title={t.saved.signInFirst}>
            {null}
          </Callout>
        ) : error ? (
          <Callout tone="warning" title={error}>
            {null}
          </Callout>
        ) : rows === null ? (
          <Card>
            <Spinner label="" />
          </Card>
        ) : rows.length === 0 ? (
          <Card>
            <p className="text-sm font-medium text-ink">{t.saved.empty}</p>
            <p className="mt-1 text-sm text-ink-soft">{t.saved.emptyHint}</p>
          </Card>
        ) : (
          <ul className="grid gap-3">
            {rows.map((row) => (
              <SavedRow key={row.id} row={row} onRemoved={load} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function SavedRow({ row, onRemoved }: { row: SavedVehicle; onRemoved: () => void }) {
  const { t, locale } = useLocale();
  const [busy, setBusy] = useState(false);

  const target = row.target_price_azn == null ? null : Number(row.target_price_azn);

  return (
    <li className="card flex items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-ink">
          {row.label ?? row.config_id ?? "—"}
        </p>
        <p className="mt-1 text-xs text-ink-muted">
          {t.saved.savedOn}: {new Date(row.created_at).toLocaleDateString(locale)}
          {target !== null ? (
            <>
              {" · "}
              {t.saved.targetPrice}:{" "}
              <span className="tnum">{target.toLocaleString(locale)} AZN</span>
            </>
          ) : null}
        </p>
      </div>

      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await removeSaved(row.id);
            onRemoved();
          } finally {
            setBusy(false);
          }
        }}
        className="min-h-[44px] shrink-0 cursor-pointer rounded-md border border-surface-border px-3
                   text-sm text-ink-soft transition-colors duration-200 hover:bg-surface-sunken
                   disabled:cursor-not-allowed disabled:opacity-60"
      >
        {t.saved.remove}
      </button>
    </li>
  );
}

/**
 * The button under a finished report.
 *
 * Hidden entirely when signed out rather than shown and then refused: an
 * action that only fails once pressed is worse than one that was never
 * offered, and the Profile tab is one click away.
 */
export function SaveVehicleButton({ analysis }: { analysis: Analysis }) {
  const { t } = useLocale();
  const { user } = useSession();
  const [state, setState] = useState<"idle" | "busy" | "done">("idle");
  const [error, setError] = useState<string | null>(null);

  if (!user) return null;

  const vehicle = analysis.vehicle;
  const label = [vehicle.model_year, vehicle.make, vehicle.model]
    .filter(Boolean)
    .join(" ");

  async function save() {
    setState("busy");
    setError(null);
    try {
      await saveVehicle({
        config_id: vehicle.configuration_id,
        analysis_id: analysis.analysis_id,
        label: label || null,
      });
      setState("done");
    } catch (caught) {
      // 409 is not a failure worth an alarm: it means the vehicle is already
      // where the user was trying to put it.
      if (caught instanceof ApiError && caught.status === 409) {
        setState("done");
        return;
      }
      setState("idle");
      setError(caught instanceof ApiError ? caught.message : String(caught));
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <button
        type="button"
        onClick={() => void save()}
        disabled={state !== "idle"}
        className="min-h-[44px] cursor-pointer rounded-md border border-surface-border px-4
                   text-sm text-ink-soft transition-colors duration-200 hover:bg-surface
                   disabled:cursor-not-allowed disabled:opacity-60"
      >
        {state === "busy" ? t.saved.saving : state === "done" ? t.saved.alreadySaved : t.saved.save}
      </button>
      {error ? <p className="text-xs text-rating-over">{error}</p> : null}
    </div>
  );
}
