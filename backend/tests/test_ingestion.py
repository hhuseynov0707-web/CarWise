"""Ingestion: crawl politeness and parser health reporting.

The parser tests here matter more than they look. A scraper does not fail
loudly when a site changes its markup — it keeps returning 200, keeps writing
rows, and quietly writes nulls into the columns the valuation depends on. These
tests assert that a broken rule is *reported per field*, which is the only
mechanism that catches that failure mode (audit §3).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import httpx
import pytest

from app.adapters.market.base import ExtractionHealth, ParseResult
from app.adapters.market.http import (
    FetchFailed,
    PoliteHttpClient,
    TokenBucket,
    _backoff_seconds,
)
from app.adapters.market.turbo import TurboAdapter, load_selectors

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def build_page(**overrides: str) -> str:
    """A listing page shaped like a real Turbo.az detail page.

    Mirrors the markup the rules were verified against on 2026-08-29: a flat
    run of name/value spans, with the values that double as search filters
    (make, model, year) wrapped in an anchor. Fuel has no property of its own
    on the real site — it is the last segment of the engine value — so it is
    rendered that way here too.

    Used to test the parser's *reporting*, not to claim the rules still match
    the live site. Whether they do is the job of ``verify_turbo``.
    """
    fields = {
        "external_id": "8471932",
        "price": "43 500 AZN",
        "make": "BMW",
        "model": "5 Series",
        "model_year": "2019",
        "mileage_km": "120 000 km",
        "city": "Bakı",
        "fuel": "Benzin",
        "transmission": "Avtomat",
        "drivetrain": "Arxa ötürücü",
        "body": "Sedan",
        "engine": "2.0 L / 249 a.g.",
        "seller_type": "Rəsmi nümayəndə",
    }
    fields.update(overrides)

    linked = {"make", "model", "model_year"}

    def row(key: str, label: str, value: str) -> str:
        inner = f'<a href="/autos?q=1">{value}</a>' if key in linked else value
        return (
            '<div class="product-properties__i">'
            f'<span class="product-properties__i-name">{label}</span>'
            f'<span class="product-properties__i-value">{inner}</span>'
            "</div>"
        )

    engine_value = fields["engine"]
    if fields.get("fuel"):
        engine_value = f"{engine_value} / {fields['fuel']}"

    rows = "".join(
        row(key, label, engine_value if key == "engine" else fields[key])
        for key, label in (
            ("make", "Marka"),
            ("model", "Model"),
            ("model_year", "Buraxılış ili"),
            ("mileage_km", "Yürüş"),
            ("city", "Şəhər"),
            ("transmission", "Sürətlər qutusu"),
            ("drivetrain", "Ötürücü"),
            ("body", "Ban növü"),
            ("engine", "Mühərrik"),
        )
        if fields.get(key)
    )

    shop = (
        f'<div class="product-shop__owner-featured">{fields["seller_type"]}</div>'
        if fields.get("seller_type")
        else ""
    )

    return f"""<html><head>
      <link rel="canonical" href="https://turbo.az/autos/{fields['external_id']}-bmw-5-series" />
    </head><body>
      <div class="product-price__i product-price__i--bold">{fields['price']}</div>
      <div class="product-properties__column">{rows}</div>
      {shop}
      <div class="product-description__content">
        <p>Vurulmayıb, rənglənməyib. Ideal vəziyyətdə.</p>
      </div>
    </body></html>"""


@pytest.fixture
def adapter() -> TurboAdapter:
    return TurboAdapter(client=None, selectors=load_selectors())  # type: ignore[arg-type]


class TestParserExtraction:
    def test_parses_a_well_formed_page(self, adapter: TurboAdapter) -> None:
        result = adapter.parse(build_page(), "https://turbo.az/autos/8471932", NOW)

        assert result.ok
        listing = result.listing
        assert listing is not None
        assert listing.external_id == "8471932"
        assert listing.price.as_float() == 43_500
        assert listing.mileage_km == 120_000
        assert listing.city == "Bakı"

    def test_resolves_the_vehicle_configuration(self, adapter: TurboAdapter) -> None:
        result = adapter.parse(build_page(), "https://turbo.az/autos/8471932", NOW)
        config = result.listing.configuration  # type: ignore[union-attr]

        assert config.make == "BMW"
        assert config.model_year == 2019
        assert config.fuel.value == "PETROL"
        assert config.transmission.value == "AUTOMATIC"
        assert config.drivetrain.value == "RWD"
        assert config.displacement_l == 2.0
        assert config.is_resolvable

    def test_reads_the_currency_rather_than_assuming_azn(self, adapter: TurboAdapter) -> None:
        """A USD price treated as AZN would be roughly a 40% error."""
        result = adapter.parse(
            build_page(price="25 000 USD"), "https://turbo.az/autos/1", NOW
        )
        assert result.listing.price.currency.value == "USD"  # type: ignore[union-attr]

    def test_a_broken_rule_is_reported_per_field(self, adapter: TurboAdapter) -> None:
        """The central health mechanism: silent degradation must be impossible."""
        page = build_page(mileage_km="", city="")
        result = adapter.parse(page, "https://turbo.az/autos/1", NOW)

        assert result.ok, "the listing should still parse"
        assert "mileage_km" in result.missing_fields
        assert "city" in result.missing_fields
        assert result.listing.mileage_km is None  # type: ignore[union-attr]

    def test_an_unparseable_price_fails_the_record(self, adapter: TurboAdapter) -> None:
        """No price means no market observation; a row without one is worthless."""
        result = adapter.parse(
            build_page().replace("--bold\">43 500 AZN<", "--bold\"><"),
            "https://turbo.az/autos/1",
            NOW,
        )
        assert not result.ok

    def test_falls_back_to_the_url_for_an_identifier(self, adapter: TurboAdapter) -> None:
        page = build_page(external_id="no code here")
        result = adapter.parse(page, "https://turbo.az/autos/9988776", NOW)
        assert result.listing.external_id == "9988776"  # type: ignore[union-attr]

    def test_unrecognised_vocabulary_is_surfaced(self, adapter: TurboAdapter) -> None:
        """A make our tables do not know must become a visible metric."""
        result = adapter.parse(
            build_page(make="Zaporozhets"), "https://turbo.az/autos/1", NOW
        )
        assert result.unmapped_values.get("make") == "Zaporozhets"


class TestSelectorGate:
    def test_shipped_rules_are_verified_against_real_pages(
        self, adapter: TurboAdapter
    ) -> None:
        """Audit §4: an unverified parser must not fill the database.

        This asserted the opposite for as long as the rules were guesses. They
        were checked against live listings on 2026-08-29, so the shipped state
        is now verified. The guard itself is unchanged and still tested — by
        the case below and by TestIngestionRefusals.
        """
        assert adapter.selectors_verified is True

    def test_verification_requires_every_rule(self) -> None:
        selectors = load_selectors()
        for spec in selectors["listing"].values():
            spec["verified"] = True
        assert TurboAdapter(client=None, selectors=selectors).selectors_verified  # type: ignore[arg-type]

        selectors["listing"]["price"]["verified"] = False
        assert not TurboAdapter(client=None, selectors=selectors).selectors_verified  # type: ignore[arg-type]


class TestExtractionHealth:
    def test_reports_per_field_rates(self, adapter: TurboAdapter) -> None:
        health = ExtractionHealth()
        for index in range(10):
            # Half the pages lose their mileage, as a markup change would cause.
            page = build_page(mileage_km="" if index % 2 else "100 000 km")
            health.record(adapter.parse(page, f"https://turbo.az/autos/{index}", NOW))

        rates = health.rates()
        assert rates["price"] == 1.0
        assert rates["mileage_km"] == pytest.approx(0.5)

    def test_degraded_fields_are_identified(self, adapter: TurboAdapter) -> None:
        health = ExtractionHealth()
        for index in range(10):
            health.record(adapter.parse(build_page(city=""), f"https://x/{index}", NOW))

        assert "city" in health.degraded_fields()
        assert "price" not in health.degraded_fields()

    def test_failure_rate_counts_unusable_records(self) -> None:
        health = ExtractionHealth()
        health.record(ParseResult(listing=None, errors=("bad",)))
        health.record(ParseResult(listing=None, errors=("bad",)))
        assert health.failure_rate == 1.0

    def test_empty_health_does_not_divide_by_zero(self) -> None:
        assert ExtractionHealth().rates() == {}
        assert ExtractionHealth().failure_rate == 0.0


class TestTokenBucket:
    def test_burst_then_throttle(self) -> None:
        """The burst is consumed immediately; the next request must wait."""

        async def run() -> float:
            bucket = TokenBucket(rate_per_second=10.0, capacity=3)
            for _ in range(3):
                await bucket.acquire()
            started = time.monotonic()
            await bucket.acquire()
            return time.monotonic() - started

        elapsed = asyncio.run(run())
        assert elapsed >= 0.05, "the fourth request was not throttled"

    def test_rejects_a_non_positive_rate(self) -> None:
        with pytest.raises(ValueError):
            TokenBucket(rate_per_second=0.0)

    def test_configured_rate_is_respected(self) -> None:
        async def run() -> float:
            bucket = TokenBucket(rate_per_second=20.0, capacity=1)
            started = time.monotonic()
            for _ in range(4):
                await bucket.acquire()
            return time.monotonic() - started

        # Three waits at 1/20s each, before jitter.
        assert asyncio.run(run()) >= 0.15


class TestBackoff:
    def test_grows_with_attempts_and_is_capped(self) -> None:
        assert _backoff_seconds(1) < _backoff_seconds(4)
        assert _backoff_seconds(20) <= 61.0


class TestIngestionRefusals:
    """Audit §4: the pipeline must refuse to run in the wrong conditions.

    These are the guards that stop a well-meaning operator from pointing an
    untested parser at a live site, so they are worth asserting explicitly.
    """

    def _service(self, **kwargs):
        from app.domain.money import FxTable
        from app.services.ingestion import IngestionService

        defaults = dict(session=None, fx=FxTable(), ingestion_enabled=True)
        defaults.update(kwargs)
        return IngestionService(**defaults)  # type: ignore[arg-type]

    def test_refuses_when_ingestion_is_disabled(self, adapter: TurboAdapter) -> None:
        from app.services.ingestion import IngestionRefused

        service = self._service(ingestion_enabled=False)
        with pytest.raises(IngestionRefused, match="terms of service"):
            service._guard(adapter)

    def test_refuses_while_selectors_are_unverified(self) -> None:
        from app.services.ingestion import IngestionRefused

        # Built unverified on purpose rather than relying on the shipped state,
        # so that verifying the rules cannot quietly retire this guard.
        selectors = load_selectors()
        selectors["listing"]["price"]["verified"] = False
        unverified = TurboAdapter(client=None, selectors=selectors)  # type: ignore[arg-type]

        service = self._service()
        with pytest.raises(IngestionRefused, match="not marked verified"):
            service._guard(unverified)

    def test_proceeds_once_selectors_are_verified(self) -> None:
        selectors = load_selectors()
        for spec in selectors["listing"].values():
            spec["verified"] = True
        verified = TurboAdapter(client=None, selectors=selectors)  # type: ignore[arg-type]

        self._service()._guard(verified)  # must not raise


class TestRunAbort:
    """A run must stop when the parser has clearly lost touch with the pages."""

    def _report(self, seen: int, failures: int):
        from app.adapters.market.base import ExtractionHealth
        from app.services.ingestion import IngestionReport

        report = IngestionReport(source="t", started_at=NOW)
        report.listings_seen = seen
        health = ExtractionHealth()
        health.total = seen
        health.failures = failures
        health.present = {name: seen - failures for name in ExtractionHealth.TRACKED_FIELDS}
        report.health = health
        return report

    def _service(self):
        from app.domain.money import FxTable
        from app.services.ingestion import IngestionService

        return IngestionService(session=None, fx=FxTable(), ingestion_enabled=True)  # type: ignore[arg-type]

    def test_small_samples_do_not_trigger_an_abort(self) -> None:
        # Ten pages, all failing, is not yet evidence of a broken parser.
        self._service()._maybe_abort(self._report(seen=10, failures=10))

    def test_collapsed_success_rate_aborts(self) -> None:
        from app.services.ingestion import _Aborted

        with pytest.raises(_Aborted, match="stopped matching"):
            self._service()._maybe_abort(self._report(seen=40, failures=30))

    def test_healthy_run_continues(self) -> None:
        self._service()._maybe_abort(self._report(seen=100, failures=2))

    def test_losing_a_critical_field_aborts(self) -> None:
        from app.adapters.market.base import ExtractionHealth
        from app.services.ingestion import IngestionReport, _Aborted

        report = IngestionReport(source="t", started_at=NOW)
        report.listings_seen = 60
        health = ExtractionHealth()
        health.total = 60
        health.failures = 0
        health.present = {name: 60 for name in ExtractionHealth.TRACKED_FIELDS}
        health.present["price"] = 5  # markup changed; price selector stopped matching
        report.health = health

        with pytest.raises(_Aborted, match="price"):
            self._service()._maybe_abort(report)


class TestDataQuality:
    """Spec §45. Bounds sit far outside anything a real listing reaches — a
    genuinely cheap car is signal to explain, not noise to discard."""

    def _listing(self, **kwargs):
        from datetime import UTC, datetime as dt

        from app.adapters.market.base import RawListing
        from app.domain.identity import VehicleConfiguration
        from app.domain.money import Money

        defaults = dict(
            external_id="1",
            source_url="https://x/1",
            configuration=VehicleConfiguration.from_raw(
                make="BMW", model="5 Series", model_year=2019
            ),
            price=Money.azn(43_500),
            observed_at=dt(2026, 8, 27, tzinfo=UTC),
            mileage_km=120_000,
        )
        defaults.update(kwargs)
        return RawListing(**defaults)  # type: ignore[arg-type]

    def test_a_clean_listing_raises_nothing(self) -> None:
        from app.services.ingestion import validate_raw_listing

        assert validate_raw_listing(self._listing()) == []

    def test_a_cheap_but_real_car_is_kept(self) -> None:
        from app.domain.money import Money
        from app.services.ingestion import validate_raw_listing

        assert validate_raw_listing(self._listing(price=Money.azn(2_500))) == []

    @pytest.mark.parametrize(
        ("kwargs", "issue"),
        [
            ({"price": None}, "IMPLAUSIBLE_PRICE"),
            ({"mileage_km": 9_000_000}, "IMPLAUSIBLE_MILEAGE"),
        ],
    )
    def test_data_errors_are_flagged(self, kwargs: dict, issue: str) -> None:
        from app.domain.money import Money
        from app.services.ingestion import validate_raw_listing

        if kwargs.get("price") is None:
            kwargs["price"] = Money.azn(4_000_000)
        issues = validate_raw_listing(self._listing(**kwargs))
        assert any(code == issue for code, _ in issues)

    def test_an_unrecognised_vehicle_is_flagged(self) -> None:
        from app.domain.identity import VehicleConfiguration
        from app.services.ingestion import validate_raw_listing

        listing = self._listing(
            configuration=VehicleConfiguration.from_raw(make="Zaporozhets", model="965")
        )
        issues = validate_raw_listing(listing)
        assert any(code == "UNRESOLVABLE_VEHICLE" for code, _ in issues)


class TestFingerprint:
    def _listing(self, price: float, mileage: int = 120_000):
        from datetime import UTC, datetime as dt

        from app.adapters.market.base import RawListing
        from app.domain.identity import VehicleConfiguration
        from app.domain.money import Money

        return RawListing(
            external_id="1",
            source_url="https://x/1",
            configuration=VehicleConfiguration.from_raw(make="BMW", model="5 Series"),
            price=Money.azn(price),
            observed_at=dt(2026, 8, 27, tzinfo=UTC),
            mileage_km=mileage,
        )

    def test_unchanged_content_hashes_identically(self) -> None:
        from app.domain.money import Money
        from app.services.ingestion import content_fingerprint

        a = content_fingerprint(self._listing(43_500), Money.azn(43_500))
        b = content_fingerprint(self._listing(43_500), Money.azn(43_500))
        assert a == b

    def test_a_price_change_changes_the_fingerprint(self) -> None:
        from app.domain.money import Money
        from app.services.ingestion import content_fingerprint

        a = content_fingerprint(self._listing(43_500), Money.azn(43_500))
        b = content_fingerprint(self._listing(42_000), Money.azn(42_000))
        assert a != b


class TestTransientNetworkFailures:
    """A momentary network fault must not end a long run.

    A backfill died after seventy-odd listings on a single failed DNS lookup,
    because a connect error was raised straight through while a timeout — the
    same fault from the caller's side — was retried. These pin the fix.
    """

    class _FlakyClient:
        """Refuses to connect a set number of times, then answers."""

        def __init__(self, failures: int) -> None:
            self.failures = failures
            self.attempts = 0

        async def get(self, url: str, headers: dict | None = None) -> httpx.Response:
            request = httpx.Request("GET", url)
            if url.endswith("/robots.txt"):
                return httpx.Response(404, request=request)
            self.attempts += 1
            if self.attempts <= self.failures:
                raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
            return httpx.Response(200, text="ok", request=request)

        async def aclose(self) -> None:
            return None

    @staticmethod
    def _client(fake) -> PoliteHttpClient:
        return PoliteHttpClient(
            user_agent="AutoIntelBot/test", requests_per_second=1000.0, client=fake
        )

    def test_a_connect_error_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(PoliteHttpClient, "_backoff", lambda self, attempt: _noop())

        fake = self._FlakyClient(failures=2)

        async def run() -> object:
            return await self._client(fake).get("https://example.test/autos/1")

        result = asyncio.run(run())
        assert result.ok  # type: ignore[attr-defined]
        assert fake.attempts == 3, "the first two failures should have been retried"

    def test_persistent_connect_errors_still_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retrying is not the same as never giving up."""
        monkeypatch.setattr(PoliteHttpClient, "_backoff", lambda self, attempt: _noop())

        fake = self._FlakyClient(failures=99)

        async def run() -> None:
            with pytest.raises(FetchFailed):
                await self._client(fake).get("https://example.test/autos/1")

        asyncio.run(run())
        assert fake.attempts == 3, "should stop at max_attempts"


async def _noop() -> None:
    return None
