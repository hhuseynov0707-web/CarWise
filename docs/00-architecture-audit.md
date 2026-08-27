# 00 — Architecture Audit

Status: pre-implementation audit. Written before code, per spec §72.
Date: 2026-08-27.

This document records what we know, what we do not know, and what the
architecture must therefore refuse to assume. It is the reference for why the
code is shaped the way it is.

---

## 1. The single hardest constraint

> The product's core claim is **"this is what the market says."**
> We do not yet have a market dataset.

Everything else follows from this. On day zero the platform has:

- no listings
- no price history
- no transaction observations
- no seasonality baseline
- no depreciation curves calibrated to Azerbaijan

A valuation engine with an empty database must not produce a number. It must
produce **"insufficient comparable data"** and say so loudly. This is spec §49
(Low-Data Mode) and it is not an edge case — it is the *default state of the
system for the first weeks of operation*, and it remains the permanent state for
long-tail configurations (rare trims, rare imports, low-volume models).

**Architectural consequence:** low-data is the primary path, not the fallback.
The valuation engine is written so that "refuse to answer" is a first-class
return value (`ValuationOutcome.INSUFFICIENT_DATA`), not an exception, not a
`None`, and never a silently-widened range around a guess.

---

## 2. Listing price is not transaction price (spec §9)

This is the deepest correctness issue in the product and it cannot be fixed
later by relabeling UI strings.

In the Azerbaijani used-car market the gap between asking price and settled
price is material and is **not a constant percentage**. It varies by:

- segment (a 6,000 AZN car and a 90,000 AZN car do not discount alike)
- seller type (private sellers generally pad more than dealers)
- days on market (the pad shrinks as the listing ages)
- season and FX conditions

Because we cannot measure that gap on day one, the system must **never**
silently apply a "negotiation haircut" to convert listing medians into an
implied transaction value. Doing so would bake an invented constant into the
core number of the product.

**Architectural consequence:**

- The valuation engine is explicitly typed as producing a **listing-market
  value** (`PriceBasis.ASKING`), not a transaction value.
- All copy derived from it says *asking* / *listed*, never *sale price*.
- `transaction_observations` exists in the schema from day one and is wired
  through the engine as an optional, higher-weight evidence source, so that when
  real transaction data arrives the basis can shift to `PriceBasis.TRANSACTION`
  **per configuration**, gated on sample size — not globally by a config flag.
- The `PriceBasis` travels with every number to the UI and into the PDF, so a
  report is always self-describing about what kind of price it discusses.

---

## 3. Data-source risk: Turbo.az dependency

Spec §6 says do not become permanently dependent on one site. The audit finding
is stronger: **we are dependent on it on day one and must architect for its loss
from day one.**

Realistic failure modes, roughly in order of likelihood:

| Failure | Likelihood | Blast radius today | Mitigation in architecture |
|---|---|---|---|
| Markup / DOM change | High, recurring | Ingestion silently degrades | Parser returns per-field success; health metrics alert on field-level extraction-rate drops, not just HTTP errors |
| Rate limiting / IP block | Medium | Full ingestion halt | Conservative crawl budget, backoff, `Retry-After` honoured, incremental-only fetching |
| ToS change or explicit prohibition | Medium | Legal exposure plus full halt | Adapter is one implementation behind an interface; a disabled adapter must not break the app |
| Structural change (auth wall) | Low-medium | Full halt | Same as above, plus partner/dealer adapters as the strategic answer |

**Architectural consequence:** `MarketSourceAdapter` is an abstract interface and
the Turbo implementation is registered in a registry, not imported directly by
any engine. No engine, service, or API route may import a concrete adapter. This
is enforced by an import-linting test, not by convention.

The strategic answer to source risk is **not** more scrapers. It is the
first-party dataset (spec §63): our own listing history, our own price-change
observations, and eventually user-confirmed transactions. Those live in our
database and cannot be taken away.

---

## 4. Legal and ethical constraints on ingestion

A real constraint, not a formality, and it shapes the pipeline design.

Rules the ingestion layer enforces **in code**, not in documentation:

1. **robots.txt is fetched, parsed, cached, and obeyed** before any request. A
   disallowed path is not fetched. There is no override flag.
2. **Rate limiting is a hard token bucket** with a conservative default
   (0.2 requests/second sustained per host, i.e. one request per 5 seconds) plus
   jitter. It lives in the HTTP client, so a new caller cannot bypass it.
3. **Identifiable User-Agent** with a contact URL. We do not disguise the
   crawler. If the site owner wants to talk to us, they can.
4. **Incremental only.** After the initial backfill we fetch listing index pages
   and re-fetch detail pages only when the summary fingerprint changed. This
   cuts request volume by roughly an order of magnitude and is also what makes
   price-change detection cheap.
5. **Conditional requests** (`ETag` / `If-Modified-Since`) where supported.
6. **No circumvention.** No CAPTCHA solving, no auth-wall bypass, no rotating
   residential proxies. If access requires circumvention, the adapter is
   disabled and the correct next step is a commercial data agreement.
7. **Facts, not expression.** We store structured facts (price, year, mileage,
   engine) plus a link back to the source listing. We do not republish
   copyright-protected listing photographs or the seller's descriptive prose as
   our own content. Descriptions are retained for *analysis* (disclosure
   detection, risk signals) with restricted display, not public reproduction.
8. **Deletion propagates.** When a source listing disappears we mark it
   `REMOVED` and stop displaying its content, while retaining the derived
   numeric observation for market statistics.

**Open item requiring a human decision, not a code decision:** whether
Turbo.az's terms of service permit automated access at all. A person must read
them, and ideally a data-sharing conversation should be opened with them. The
code is written so that the answer "no" costs us an adapter, not a product.
**Do not run the Turbo adapter against production until this is signed off** —
the repo ships with ingestion disabled by default (`INGESTION_ENABLED=false`).

---

## 5. Where a language model is allowed to touch the system

Spec §31, §32 and §66 are correct. Stated as invariants we can test:

> **No number that reaches the user may originate from a language model.**

Grok's input is a fully-computed evidence bundle. Its output is prose plus
references to numbers *that already exist in the bundle*. The validation layer
re-checks every numeric field of the model's JSON against the bundle and rejects
the response if a number was invented or altered. That check is code
(`app/adapters/llm/validation.py`), not a prompt instruction, because prompt
instructions are not a security boundary.

Second invariant:

> **The system must produce a complete, correct, useful report with the LLM
> switched off.**

If Grok is unavailable the report degrades to the structured evidence view — all
the numbers, comparables, risk signals and inspection priorities, minus the
narrative prose. That is graceful degradation, and it is also how the valuation
engine gets tested (spec §72: "independently testable without Grok").

---

## 6. Vehicle identity is the load-bearing wall

Everything downstream — comparables, market statistics, trends, dealer
analytics — keys off configuration identity. If identity resolution is weak,
every number in the product is quietly wrong in a way that looks plausible.

Hazards specific to this market:

- **Transliteration and mixed script.** Listings appear with Azerbaijani, Russian
  and English spellings of the same make or model. "Мерседес", "Mercedes" and
  "Mersedes" are one make. Normalization needs a curated synonym table, not
  `str.lower()`.
- **Grey imports.** Vehicles imported from the US, Georgia, UAE and Europe carry
  region-specific trims absent from the local factory catalogue. A US-spec Camry
  SE and a Gulf-spec Camry are not the same comparable.
- **Trim ambiguity.** Sellers write trims inconsistently or omit them entirely.
  Identity must represent "trim unknown" distinctly from "base trim" —
  conflating those two corrupts comparables in a systematically downward
  direction.
- **VIN gaps.** Many local listings carry no VIN at all. Identity must resolve
  from attributes alone, at reduced confidence, and the confidence engine must
  see that reduction.

**Architectural consequence:** identity produces a *ladder* of keys, not one ID:
`config_id` → `powertrain_key` → `generation_key` → `model_key`. The comparable
engine walks down this ladder deterministically when the tightest key is too
sparse, and reports which rung it landed on. Widening the search is therefore
visible evidence in the report, not a hidden fallback.

`UNKNOWN` is a first-class value in every identity field. It is never coerced to
a default.

---

## 7. Statistical hazards specific to this domain

Recorded because they are easy to get wrong and expensive to discover late.

1. **Price distributions are right-skewed.** Use median and weighted quantiles,
   never the mean, as the central estimate. Mean is reported only as a
   diagnostic.
2. **Sample contamination.** Dealer listings, duplicate cross-postings, and
   damaged or salvage vehicles listed beside clean ones all distort the
   comparable set. Deduplication and outlier handling happen *before*
   statistics, and outlier removal must be robust (MAD-based), because one
   mistyped 4,000,000 AZN listing destroys a mean and badly damages an
   unweighted standard deviation.
3. **The mileage/price slope is not a constant.** It differs by segment and by
   age. Deriving it *from the comparable set itself* is correct; importing a
   global "X AZN per 1,000 km" constant is inventing data. Where the comparable
   set is too small to fit a slope, the adjustment returns zero **with a stated
   reason**, not a guessed value.
4. **Seasonality needs at least a year of history.** Until then the seasonal
   adjustment returns zero with reason `INSUFFICIENT_HISTORY`. It must not be
   estimated from three months of data.
5. **Survivorship bias in listing data.** Cars that sell quickly leave the
   dataset quickly, so a snapshot of *currently active* listings is biased
   toward overpriced and hard-to-sell vehicles. Market statistics are therefore
   computed over listings *observed within a window* (including ones since
   removed), not over the active set only. This bias is real and it pushes
   medians up.
6. **Confidence must be calibrated, not decorative.** A stated 87% that does not
   correspond to roughly 87% interval coverage on held-out data is a lie with a
   decimal point. Spec §46 monitoring exists to catch exactly this. Until we
   have held-out data, confidence is described in terms of its *inputs* (sample
   size, similarity, freshness, completeness) rather than implying a validated
   probability.

---

## 8. Layer boundaries (spec §66) as enforceable rules

```
domain/       Pure types plus identity.        May import: domain
engines/      Pure computation over domain.    May import: domain, engines
adapters/     I/O with the outside world.      May import: domain, engines, adapters
db/           Persistence.                     May import: domain, db
services/     Orchestration; wires the above.  May import: all but api
api/          Transport; computes nothing.     May import: all
```

The direction that carries the weight is **engines must never depend on
adapters**. That is what keeps the analytical core free of I/O, and it is the
rule that makes ground-truth testing possible. The reverse direction is fine and
expected — an adapter serializing an analysis has to know the analysis types.

Two further rules, both enforced:

- **No module outside the composition root may import a concrete adapter**
  (`grok.py`, `turbo.py`, …). Everything depends on the abstract port, which is
  what makes the LLM provider, the market source and the history provider
  swappable per spec §72.
- **The analytical core may not read the wall clock.** Engines receive `as_of`
  as a parameter. A `datetime.now()` inside an engine would make its output
  irreproducible and untestable, so the test greps for it.

The valuable property is that `domain/` and `engines/` have **no I/O, no
database, no network, no clock and no LLM**. That makes the entire analytical
core deterministic and unit-testable with plain fixtures, which is what makes
§46 (model monitoring) and §72 ("independently testable without Grok")
achievable rather than aspirational.

`tests/test_architecture.py` enforces these import rules automatically.

---

## 9. Deliberate scope cuts for Phase 1

Not because they are unimportant, but because they depend on data we do not have
or add risk without validating the core hypothesis.

| Deferred | Reason |
|---|---|
| XGBoost / LightGBM valuation | Needs thousands of labelled rows. The baseline comparable model is the correct Phase-1 estimator and is also the benchmark any ML model must later beat. |
| Seasonality adjustment | Needs at least a year of history. Returns 0 with a reason until then. |
| Transaction-basis valuation | Needs contributed transaction data. Schema and code path exist; basis switches per configuration on sample size. |
| Image analysis / OCR | Phase 2. Interfaces defined so it plugs in as another evidence source with `AI_INTERPRETED` provenance. |
| Vehicle history providers | No confirmed provider for the Azerbaijani market yet. Adapter interface defined; absence is reported honestly, never fabricated. |

---

## 10. Audit conclusions carried into the code

1. Low-data is the default path. `INSUFFICIENT_DATA` is a real return value.
2. Listing price is typed distinctly from transaction price, and a `PriceBasis`
   travels with every number.
3. No engine imports a concrete adapter. Sources are replaceable.
4. Crawl politeness is enforced by the HTTP client, not by caller discipline.
5. No user-visible number originates from a language model; validation is code.
6. The report is complete and useful with the LLM disabled.
7. Identity is a ladder of keys; `UNKNOWN` is never coerced to a default.
8. Adjustments that lack data return **zero with a reason**, never a guess.
9. Statistics are computed over observed-in-window listings to fight
   survivorship bias.
10. Every score decomposes into named contributions (spec §69).
