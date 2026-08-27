# AutoIntel Azerbaijan

**Vehicle market intelligence for the Azerbaijani used-car market.**

> Know the car. Know the market. Decide yourself.

The product answers what a vehicle is, what comparable vehicles cost locally,
how an asking price compares, what risks are visible in the available data, what
should be verified, and what to ask before buying. It does not tell anyone
whether to buy.

---

## The one architectural claim

**No number that reaches a user originates from a language model.**

Comparable selection, valuation, risk scoring, confidence and the deal rating
are computed by statistical engines. The reasoning layer receives a
fully-computed evidence bundle and writes prose about it. Every figure in its
output is re-checked against that bundle in code before anything is returned,
and a response containing an invented number is rejected and retried.

Turn the language model off entirely and the report is still complete — all the
numbers, comparables, risk signals, questions and inspection priorities, minus
the narrative. That is the invariant, and it is exercised by a test rather than
asserted in a document.

---

## Run it

### Fastest path — see the whole pipeline with nothing installed but Python

```bash
cd backend && python scripts/demo_analysis.py
```

Runs comparable selection, valuation, risk, confidence, rating, negotiation,
inspection and narrative generation against a **synthetic** market whose true
price model is known, then prints the report and checks the estimate against
that ground truth. No database, no API key, no network.

The market is synthetic and labelled as such in the output. That is the point:
it demonstrates the machinery *and* demonstrates the machinery gets the right
answer when the right answer is known.

### The full stack

```bash
cp .env.example .env
docker compose up -d postgres redis
```

```bash
cd backend && pip install -e ".[dev]" && alembic upgrade head
```

```bash
cd backend && uvicorn app.main:app --reload
```

```bash
cd frontend && npm install && npm run dev
```

API at `http://localhost:8000` (docs at `/docs`), web at `http://localhost:3000`.

A fresh database has no listings, so every analysis will correctly return
`INSUFFICIENT_DATA` until market data exists. That is the system working, not
failing — see *Low-data mode* below.

### Tests

```bash
cd backend && python -m pytest -q
```

---

## How the valuation actually works

Not a black box, and not a language model. A **normalize-then-aggregate**
estimator:

1. **Fit correction slopes from the comparable set itself.** The price effect of
   mileage and model year is measured from the comparable listings, using a
   Theil–Sen fit (median of pairwise slopes) so that a few contaminated listings
   cannot drag it.
2. **Normalize every comparable to the subject.** Restate each observed price as
   "what this car would be asking at the subject's mileage and model year".
3. **Weighted median of the normalized prices** becomes the central estimate.
4. **Weighted quantiles** become the range, widened by `sqrt(1 + 1/n_eff)` so a
   thin sample produces a visibly wider range rather than false precision.
5. **Attribute the movement by ablation** — recompute with one factor switched
   off and report the difference. That gives per-factor explainability from the
   baseline model, without waiting for a gradient-boosted one.

Step 1 is what keeps this honest. Depreciation curves for this market do not
exist in any table we own, so importing a constant "X AZN per 1,000 km" would be
inventing data. When the sample cannot support a fit, the adjustment returns
**exactly zero with a stated reason** rather than a guess.

Verified in `tests/test_valuation.py`: given a synthetic market built on a hidden
mileage slope of −0.09 AZN/km, the engine recovers ≈ −0.099 and lands within
~4% of the true value, with the true value inside the reported range.

---

## Things that were deliberately not done

Being explicit about these, because each one could look like an oversight.

| Not built | Why |
|---|---|
| **VIN decoding** returns `501` | Freely-available decoders cover the US market well and the European and Gulf-market vehicles common here poorly. A stub returning fabricated specifications would be far worse than an honest gap. |
| **Listing-URL analysis** returns `501` | Fetching an arbitrary third-party listing on demand is the access pattern ingestion is careful to avoid. It must not ship before the source-terms question below is settled. |
| **Turbo.az selectors are unverified** | They are conventional marketplace patterns, not the result of inspecting live pages. Every rule is marked `"verified": false` and ingestion refuses to run until they are checked. Writing selectors and presenting them as working would have been a guess dressed as a fact. |
| **Ingestion ships disabled** | Automated access to a third-party site is a legal and relationship decision. See below. |
| **Seasonality and demand adjustments** | Need ≥ 1 year of history. They return zero with `INSUFFICIENT_HISTORY` rather than being estimated from three months of data. |
| **XGBoost / LightGBM valuation** | Needs thousands of labelled rows. The comparable model is the correct Phase-1 estimator and is also the benchmark any ML model must beat on held-out data before replacing it. |
| **Foreign-currency asking prices** rejected with `422` | Roughly a third of local listings quote USD. Converting at a guessed rate would corrupt the comparison, so the API refuses until an FX source is wired rather than silently dropping the price. |
| **Confidence is not calibrated** | It measures evidence strength — sample size, similarity, freshness, completeness — and says so. A stated 87% that does not correspond to ~87% interval coverage is a lie with a decimal point. |

---

## Before enabling ingestion

**This needs a human decision, not a code change.**

1. Read Turbo.az's terms of service and decide whether automated access is
   permitted. Ideally open a data-sharing conversation with them.
2. Record that decision somewhere durable.
3. Verify the extraction rules against real pages:

```bash
cd backend && python -m app.adapters.market.verify_turbo https://turbo.az/autos/EXAMPLE
```

That fetches **one** page — obeying robots.txt and the rate limit — and reports
rule by rule what matched and what it captured. Correct the patterns in
`app/adapters/market/selectors.json`, set `"verified": true` on each, and re-run
until clean.

4. Only then set `INGESTION_ENABLED=true`.

What the crawler enforces in code, with no override:

- robots.txt fetched, parsed, cached and obeyed; a disallowed path is never
  requested
- a hard token bucket at 0.2 req/s sustained (one request per five seconds),
  capped at 2 req/s regardless of configuration
- `Retry-After` honoured on 429 and 503
- an identifiable User-Agent with a contact URL — the crawler is not disguised
- conditional requests, and incremental fetching after the initial backfill
- **no circumvention of anything.** If access requires it, the adapter is
  disabled and the next step is a commercial agreement, not a workaround

We store structured facts plus a link back to the source. We do not republish
listing photographs or seller prose as our own content.

---

## Low-data mode is the default path

On day zero there are no listings, no price history and no transaction
observations. A valuation engine with an empty database must not produce a
number — and long-tail configurations stay in that state permanently.

So `INSUFFICIENT_DATA` is a first-class return value, not an exception and never
a silently-widened range around a guess. The response says how many comparables
were found, why that was not enough, and what the user could supply to improve
it.

---

## Asking price is not transaction price

The deepest correctness issue in the product, and it cannot be fixed later by
relabelling UI strings.

The gap between asking and settled price is material and is **not a constant
percentage** — it varies by segment, seller type, days on market and FX
conditions. Since we cannot measure it yet, the system never applies a
"negotiation haircut" to convert listing medians into an implied transaction
value. That would bake an invented constant into the core number.

Instead a `PriceBasis` travels with every figure to the UI and into the report.
`transaction_observations` exists in the schema from day one, and valuation
switches to a transaction basis **per configuration** once enough real sale
prices exist — gated on sample size, not on a global flag.

Where negotiating room is discussed, the figure quoted is the **observed median
price movement of comparable listings**, measured from our own listing history.
That is the closest honest proxy available until real transaction data arrives.

---

## Layout

```
backend/
  app/
    domain/      Pure types, identity, money, provenance.   No I/O.
    engines/     Comparables, valuation, risk, confidence,  No I/O.
                 rating, negotiation, inspection, evidence.
    adapters/    LLM, market sources, polite HTTP client.
    db/          SQLAlchemy models, repositories, sessions.
    services/    Orchestration: analysis, ingestion, snapshots.
    api/         FastAPI routes, admin endpoints, mappers.
  tests/         311 tests, including ground-truth recovery.
frontend/        Next.js 15, TypeScript, Tailwind.
n8n/workflows/   Scheduled ingestion, snapshots, alerts (§51).
docs/            Architecture audit and design records.
```

`domain/` and `engines/` have no I/O, no database, no network, **no clock** and
no language model. Engines receive `as_of` as a parameter. That is what makes
the analytical core deterministic and testable against a known ground truth, and
`tests/test_architecture.py` enforces it by parsing every module's imports — a
layering rule that lives only in a document decays within weeks.

---

## Operator endpoints

`POST /api/v1/admin/ingestion/run`, `POST /api/v1/admin/snapshots/build`,
`GET /api/v1/admin/market/overview`.

All require `X-Admin-Key`. With `ADMIN_API_KEY` unset they return **503, not
200** — they are disabled rather than open.

The n8n workflows in `n8n/workflows/` call these on a schedule. Note what
workflow A alerts on: **per-field extraction rates**, not error counts. A
scraper does not fail loudly when a site changes its markup — it keeps returning
200, keeps writing rows, and quietly writes nulls into the columns the valuation
depends on. Error counts miss that completely.

---

## Where the moat is

Not the language model — everyone has one of those.

It is the accumulating first-party dataset: historical listings, price changes,
days on market, configuration coverage, and eventually user-confirmed
transactions. That lives in our database, gets more valuable the longer the
platform runs, and cannot be taken away by a source changing its terms.

Which is also why listing *history* is a separate table rather than a JSON
column, and why snapshots aggregate listings observed **within a window**
including removed ones — cars priced well leave the active set fastest, so a
snapshot of live listings is a snapshot of the market's leftovers, biased upward.

---

## Further reading

- [`docs/00-architecture-audit.md`](docs/00-architecture-audit.md) — the
  pre-implementation audit: what we know, what we don't, and what the
  architecture therefore refuses to assume.
