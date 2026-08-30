"use client";

import { useRef, useState } from "react";
import { AnalysisReport } from "@/components/AnalysisReport";
import { AppShell, useTabFromHash } from "@/components/AppShell";
import { VehicleForm } from "@/components/VehicleForm";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, analyseManual } from "@/lib/api";
import { useLocale } from "@/lib/locale";
import { TAB_STATUS, type TabId } from "@/lib/tabs";
import type { Analysis, ManualVehicleInput } from "@/lib/types";

export default function HomePage() {
  const [tab, setTab] = useTabFromHash();

  return (
    <AppShell active={tab} onSelect={setTab}>
      {TAB_IDS_RENDER.map((id) => (
        <section
          key={id}
          role="tabpanel"
          id={`panel-${id}`}
          aria-labelledby={`tab-${id}`}
          hidden={id !== tab}
        >
          {id === tab ? <TabPanel id={id} /> : null}
        </section>
      ))}
    </AppShell>
  );
}

const TAB_IDS_RENDER: TabId[] = [
  "analyse",
  "discover",
  "deals",
  "chat",
  "saved",
  "profile",
];

function TabPanel({ id }: { id: TabId }) {
  if (id === "analyse") return <AnalyseTab />;
  return <Unbuilt id={id} />;
}

/**
 * A destination that is navigable but not yet implemented.
 *
 * It says which of three different things is true — the work is not done, it
 * needs an account, or it needs a key — because "coming soon" tells a user
 * nothing about whether waiting will help. No sample data is rendered: a deal
 * list that turns out to be decoration is worse than an empty screen in a
 * product whose whole claim is that nothing is invented.
 */
function Unbuilt({ id }: { id: TabId }) {
  const { t } = useLocale();
  const status = TAB_STATUS[id];

  const detail =
    status === "needs-account"
      ? t.states.needsAccount
      : status === "needs-key"
        ? t.states.needsKey
        : null;

  return (
    <div>
      <h1 className="text-figure-lg font-semibold tracking-tight text-ink">{t.nav[id]}</h1>
      <p className="mt-2 text-sm text-ink-soft">{t.navHint[id]}</p>
      <div className="mt-6">
        <Callout tone="info" title={t.states.notBuiltTitle}>
          {detail}
        </Callout>
      </div>
    </div>
  );
}

function AnalyseTab() {
  const { locale, t } = useLocale();
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  async function handleSubmit(vehicle: ManualVehicleInput) {
    setLoading(true);
    setError(null);
    try {
      const result = await analyseManual(vehicle, locale);
      setAnalysis(result);
      // Move focus and view to the result rather than leaving the user to
      // discover that something appeared below the fold.
      requestAnimationFrame(() =>
        resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    } catch (caught) {
      setAnalysis(null);
      setError(caught instanceof ApiError ? caught.message : t.states.failed);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <header className="mb-8">
        <h1 className="text-figure-xl font-semibold tracking-tight text-ink">
          {t.tagline.lead}
          <br />
          <span className="text-ink-muted">{t.tagline.emphasis}</span>
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-ink-soft">{t.intro}</p>
      </header>

      <div className="no-print">
        <VehicleForm onSubmit={handleSubmit} disabled={loading} />
      </div>

      {loading ? (
        <div className="mt-6">
          <Card>
            <Spinner label={t.states.analysing} />
          </Card>
        </div>
      ) : null}

      {error ? (
        <div className="mt-6 no-print">
          <Callout tone="warning" title={t.states.failed}>
            {error}
          </Callout>
        </div>
      ) : null}

      <div ref={resultRef} className="mt-8">
        {analysis ? <AnalysisReport analysis={analysis} /> : null}
      </div>

      {analysis ? (
        <div className="mt-6 flex justify-end no-print">
          <button
            type="button"
            onClick={() => window.print()}
            className="min-h-[44px] cursor-pointer rounded-md border border-surface-border px-4
                       text-sm text-ink-soft transition-colors duration-200 hover:bg-surface"
          >
            {t.actions.print}
          </button>
        </div>
      ) : null}
    </div>
  );
}
