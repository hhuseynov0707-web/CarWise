"use client";

import { useRef, useState } from "react";
import { AnalysisReport } from "@/components/AnalysisReport";
import { VehicleForm } from "@/components/VehicleForm";
import { Callout, Card, Spinner } from "@/components/ui";
import { ApiError, analyseManual } from "@/lib/api";
import type { Analysis, ManualVehicleInput } from "@/lib/types";

export default function HomePage() {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const resultRef = useRef<HTMLDivElement>(null);

  async function handleSubmit(vehicle: ManualVehicleInput) {
    setLoading(true);
    setError(null);
    try {
      const result = await analyseManual(vehicle, "en");
      setAnalysis(result);
      // Move focus and view to the result rather than leaving the user to
      // discover that something appeared below the fold.
      requestAnimationFrame(() =>
        resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    } catch (caught) {
      setAnalysis(null);
      setError(
        caught instanceof ApiError
          ? caught.message
          : "Something went wrong while running the analysis.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <main id="main" className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-12">
      <header className="mb-8">
        <p className="text-xs font-medium uppercase tracking-widest text-ink-muted">
          AutoIntel Azerbaijan
        </p>
        <h1 className="mt-2 text-figure-xl font-semibold tracking-tight text-ink">
          Know the car. Know the market.
          <br />
          <span className="text-ink-muted">Decide yourself.</span>
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-ink-soft">
          Enter a vehicle and we will compare it against listings in the local market,
          estimate what it is worth, show where the asking price sits, and set out what
          the evidence does and does not establish. Every figure is computed from market
          data and shown with its derivation. The decision stays with you.
        </p>
      </header>

      <div className="no-print">
        <VehicleForm onSubmit={handleSubmit} disabled={loading} />
      </div>

      {loading ? (
        <div className="mt-6">
          <Card>
            <Spinner label="Selecting comparables, fitting the market model, checking risk indicators…" />
          </Card>
        </div>
      ) : null}

      {error ? (
        <div className="mt-6 no-print">
          <Callout tone="warning" title="Could not complete the analysis">
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
            className="rounded-md border border-surface-border px-4 py-2 text-sm
                       text-ink-soft transition hover:bg-surface"
          >
            Print or save as PDF
          </button>
        </div>
      ) : null}

      {!analysis && !loading ? <HowItWorks /> : null}

      <footer className="mt-12 border-t border-surface-border pt-6 text-xs text-ink-muted">
        <p>
          AutoIntel is a market-intelligence tool. It does not guarantee the mechanical
          condition, accident history, legal status or future reliability of any
          vehicle, and it is not a substitute for an independent inspection.
        </p>
      </footer>
    </main>
  );
}

function HowItWorks() {
  const steps: Array<{ title: string; body: string }> = [
    {
      title: "Comparables, not averages",
      body:
        "We match on the full configuration — generation, engine, gearbox, drivetrain, trim — and report which level of match the sample required. Two cars are not comparable just because the make, model and year agree.",
    },
    {
      title: "The market sets the adjustments",
      body:
        "The effect of mileage and model year is measured from the comparable listings themselves, not from an assumed depreciation table. Where the sample cannot support that measurement, the adjustment is zero and the report says why.",
    },
    {
      title: "A range, not a false precision",
      body:
        "The estimate is a range whose width reflects how much the market actually agrees. A thin or scattered sample produces a visibly wider range rather than a confident-looking single number.",
    },
    {
      title: "Risk indicators, not verdicts",
      body:
        "The risk score measures the strength and number of indicators found in the available data. It is not a probability that the car is bad, and each indicator states the evidence behind it and how you can check it yourself.",
    },
    {
      title: "Nothing is invented",
      body:
        "When there is not enough data, the report says so instead of producing a number. Any AI-written summary is checked against the computed evidence before it is shown, and is labelled as AI-written.",
    },
  ];

  return (
    <section className="mt-10 no-print">
      <h2 className="section-title mb-4">How this works</h2>
      <div className="grid gap-4 sm:grid-cols-2">
        {steps.map((step) => (
          <div key={step.title} className="card">
            <h3 className="text-sm font-semibold text-ink">{step.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-ink-soft">{step.body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
