"""HTTP layer, exercised end-to-end against an in-memory market.

No PostgreSQL, no network, no language model. The whole request path — schema
validation, subject construction, the analysis pipeline, response mapping — runs
in-process, which is what makes it practical to assert on the *contract* rather
than just on status codes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters.llm.service import ReasoningService
from app.adapters.market.base import AdapterRegistry
from app.config import Settings
from app.container import Container
from app.db.session import Database
from app.domain.money import FxTable
from app.engines.comparables.engine import SelectionPolicy
from app.main import create_app
from app.services.repositories import StaticRepositoryProvider
from tests.factories import synthetic_market

API = "/api/v1"


@pytest.fixture
def client() -> TestClient:
    """An app wired to a synthetic market and no reasoning provider."""
    listings, _ = synthetic_market(count=60)
    settings = Settings(
        environment="local",
        reasoning_enabled=False,
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        rate_limit_per_minute=10_000,
    )
    app = create_app(settings)

    # Replace the container that lifespan would build. The database is
    # constructed but never connected to — no route in this test touches it.
    app.dependency_overrides = {}
    container = Container(
        settings=settings,
        database=Database(settings.database_url),
        reasoning=ReasoningService(None, enabled=False),
        selection_policy=SelectionPolicy(),
        repositories=StaticRepositoryProvider(listings),
        # No ingestion adapters and no exchange rates: this fixture exercises
        # the analysis path, which touches neither.
        market_sources=AdapterRegistry(),
        fx_table=FxTable(),
    )

    with TestClient(app) as test_client:
        test_client.app.state.container = container
        yield test_client


def _payload(**overrides: object) -> dict:
    vehicle = {
        "make": "BMW",
        "model": "5 Series",
        "model_year": 2019,
        "trim": "530I",
        "displacement": 2.0,
        "fuel": "Benzin",
        "transmission": "Avtomat",
        "drivetrain": "Arxa ötürücü",
        "body": "Sedan",
        "mileage_km": 120_000,
        "asking_price": 41_000,
        "city": "Bakı",
    }
    vehicle.update(overrides)
    return {"vehicle": vehicle, "language": "en", "include_narrative": True}


class TestSystemEndpoints:
    def test_health_reports_subsystem_state_truthfully(self, client: TestClient) -> None:
        response = client.get(f"{API}/health")
        assert response.status_code == 200
        body = response.json()
        assert body["reasoning"] == "disabled"
        assert body["ingestion"] == "disabled"

    def test_reference_data_serves_ui_vocabulary(self, client: TestClient) -> None:
        response = client.get(f"{API}/reference")
        assert response.status_code == 200
        body = response.json()
        assert "BMW" in body["makes"]
        assert "Bakı" in body["cities"]
        assert "PETROL" in body["fuels"]
        assert "UNKNOWN" not in body["fuels"]

    def test_security_headers_are_present(self, client: TestClient) -> None:
        response = client.get(f"{API}/reference")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Request-ID"]


class TestManualAnalysis:
    def test_returns_a_complete_analysis(self, client: TestClient) -> None:
        response = client.post(f"{API}/analysis/manual", json=_payload())
        assert response.status_code == 200
        body = response.json()

        assert body["valuation"]["outcome"] == "OK"
        assert body["valuation"]["central_estimate"]["amount"] > 0
        assert body["price_position"]["rating"]
        assert body["risk"]["score"] >= 0
        assert body["confidence"]["score_percent"] > 0
        assert body["comparables"]
        assert body["seller_questions"]
        assert body["inspection_priorities"]
        assert body["limitations"]

    def test_every_headline_number_carries_its_reasoning(self, client: TestClient) -> None:
        """Spec §55: a badge is never rendered without the "why" beside it."""
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()

        assert body["price_position"]["rationale"], "rating has no rationale"
        assert body["confidence"]["components"], "confidence has no breakdown"
        assert body["valuation"]["adjustments"], "valuation has no adjustments"
        for component in body["confidence"]["components"]:
            assert component["explanation"]

    def test_price_basis_travels_with_the_numbers(self, client: TestClient) -> None:
        """Spec §9: a figure must always say what kind of price it is."""
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()
        assert body["valuation"]["price_basis"] == "ASKING"
        assert any("asking price" in note.lower() for note in body["valuation"]["notes"])

    def test_disclaimer_is_always_present(self, client: TestClient) -> None:
        """Spec §59: no client can render a report without it."""
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()
        assert "does not guarantee" in body["disclaimer"]

    def test_confidence_declares_it_is_uncalibrated(self, client: TestClient) -> None:
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()
        assert body["confidence"]["calibrated"] is False

    def test_narrative_is_labelled_as_non_ai_when_disabled(self, client: TestClient) -> None:
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()
        assert body["narrative"]["generated_by"] == "fallback"
        assert body["narrative"]["is_ai_generated"] is False
        assert body["narrative"]["final_assessment"]

    def test_gap_analysis_identity_holds_over_the_wire(self, client: TestClient) -> None:
        body = client.post(f"{API}/analysis/manual", json=_payload()).json()
        gap = body["price_position"]["gap_analysis"]
        assert gap is not None
        assert gap["total_gap_azn"] == pytest.approx(
            gap["explained_azn"] + gap["unexplained_azn"], abs=1
        )

    def test_cross_script_input_resolves(self, client: TestClient) -> None:
        """A Russian-language submission must reach the same comparable set."""
        russian = client.post(
            f"{API}/analysis/manual",
            json=_payload(
                make="бмв",
                model="5er",
                fuel="бензин",
                transmission="автомат",
                drivetrain="задний привод",
                body="седан",
            ),
        )
        assert russian.status_code == 200
        latin = client.post(f"{API}/analysis/manual", json=_payload()).json()
        assert russian.json()["vehicle"]["configuration_id"] == latin["vehicle"]["configuration_id"]

    def test_narrative_can_be_skipped(self, client: TestClient) -> None:
        payload = _payload()
        payload["include_narrative"] = False
        body = client.post(f"{API}/analysis/manual", json=payload).json()
        assert body["valuation"]["outcome"] == "OK"


class TestLowDataPath:
    def test_unknown_model_returns_insufficient_data_not_a_guess(
        self, client: TestClient
    ) -> None:
        """Audit §1: a thin market must refuse, loudly and with a reason."""
        body = client.post(
            f"{API}/analysis/manual", json=_payload(model="Isetta", trim=None)
        ).json()

        assert body["valuation"]["outcome"] == "INSUFFICIENT_DATA"
        assert body["valuation"]["central_estimate"] is None
        assert body["valuation"]["insufficient_reason"]
        assert body["price_position"]["rating"] == "INSUFFICIENT_DATA"

    def test_missing_mileage_is_reported_as_a_limitation(self, client: TestClient) -> None:
        body = client.post(f"{API}/analysis/manual", json=_payload(mileage_km=None)).json()
        assert any("odometer" in text.lower() for text in body["limitations"])
        assert any("mileage" in action.lower() for action in body["confidence"]["improvements"])


class TestInputValidation:
    def test_unrecognised_make_is_rejected_with_guidance(self, client: TestClient) -> None:
        response = client.post(f"{API}/analysis/manual", json=_payload(make="Notacarbrand"))
        assert response.status_code == 422
        assert "not recognised" in response.json()["detail"]

    def test_foreign_currency_is_rejected_rather_than_silently_dropped(
        self, client: TestClient
    ) -> None:
        """Converting at a guessed rate would corrupt the comparison."""
        response = client.post(f"{API}/analysis/manual", json=_payload(currency="USD"))
        assert response.status_code == 422
        assert "exchange-rate" in response.json()["detail"]

    def test_implausible_mileage_is_rejected(self, client: TestClient) -> None:
        response = client.post(f"{API}/analysis/manual", json=_payload(mileage_km=1_900_000))
        assert response.status_code == 422

    def test_invalid_vin_characters_are_rejected(self, client: TestClient) -> None:
        response = client.post(
            f"{API}/analysis/manual", json=_payload(vin="IOQIOQIOQIOQIOQIO")
        )
        assert response.status_code == 422

    def test_unknown_fields_are_rejected(self, client: TestClient) -> None:
        payload = _payload()
        payload["vehicle"]["surprise"] = "value"
        assert client.post(f"{API}/analysis/manual", json=payload).status_code == 422


class TestUnimplementedModes:
    @pytest.mark.parametrize("path", ["/analysis/vin", "/analysis/listing"])
    def test_unbuilt_modes_return_501_rather_than_fabricating(
        self, client: TestClient, path: str
    ) -> None:
        """An honest 501 beats a stub returning invented specifications."""
        response = client.post(f"{API}{path}", json={})
        assert response.status_code == 501
        assert "not yet available" in response.json()["detail"]


class TestAdminAuthentication:
    """Operator endpoints must fail closed.

    An admin route that ships open because a variable was forgotten is a worse
    failure than one that refuses to work until it is configured: the second is
    noticed on the first call, the first is noticed by whoever finds it.
    """

    ADMIN_PATHS = [
        ("POST", "/admin/ingestion/run"),
        ("POST", "/admin/snapshots/build"),
        ("GET", "/admin/market/overview"),
    ]

    @pytest.mark.parametrize(("method", "path"), ADMIN_PATHS)
    def test_disabled_when_no_key_is_configured(
        self, client: TestClient, method: str, path: str
    ) -> None:
        response = client.request(method, f"{API}{path}", json={})
        assert response.status_code == 503
        assert "ADMIN_API_KEY" in response.json()["detail"]

    @pytest.mark.parametrize(("method", "path"), ADMIN_PATHS)
    def test_rejects_a_missing_or_wrong_key(
        self, keyed_client: TestClient, method: str, path: str
    ) -> None:
        assert client_status(keyed_client, method, path, key=None) == 401
        assert client_status(keyed_client, method, path, key="wrong-key") == 401

    def test_unknown_source_is_a_404_not_a_silent_no_op(
        self, keyed_client: TestClient
    ) -> None:
        response = keyed_client.post(
            f"{API}/admin/ingestion/run",
            json={"source": "not-a-registered-source"},
            headers={"X-Admin-Key": ADMIN_KEY},
        )
        assert response.status_code == 404

    def test_admin_routes_are_not_rate_limited_into_uselessness(
        self, keyed_client: TestClient
    ) -> None:
        """A scheduled job must not be throttled out of its own maintenance window."""
        for _ in range(5):
            status_code = client_status(
                keyed_client, "GET", "/admin/market/overview", key=ADMIN_KEY
            )
            assert status_code != 429


ADMIN_KEY = "test-admin-key-that-is-long-enough-to-be-real"


def client_status(client: TestClient, method: str, path: str, key: str | None) -> int:
    headers = {"X-Admin-Key": key} if key else {}
    return client.request(method, f"{API}{path}", json={}, headers=headers).status_code


@pytest.fixture
def keyed_client() -> TestClient:
    """An app with an admin key configured, so auth itself can be exercised."""
    listings, _ = synthetic_market(count=20)
    settings = Settings(
        environment="local",
        reasoning_enabled=False,
        admin_api_key=ADMIN_KEY,
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        rate_limit_per_minute=10_000,
    )
    app = create_app(settings)
    container = Container(
        settings=settings,
        database=Database(settings.database_url),
        reasoning=ReasoningService(None, enabled=False),
        selection_policy=SelectionPolicy(),
        repositories=StaticRepositoryProvider(listings),
        market_sources=AdapterRegistry(),
        fx_table=FxTable(),
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.app.state.container = container
        yield test_client
