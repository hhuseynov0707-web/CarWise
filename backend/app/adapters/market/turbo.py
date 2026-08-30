"""Turbo.az market adapter (spec §7).

**Read this before enabling it.**

Two things about this adapter are deliberate and should not be "fixed":

1. **It ships disabled** (``INGESTION_ENABLED=false``). Automated access to a
   third-party site is a legal and relationship decision. Somebody has to read
   Turbo.az's terms of service, decide, and record that decision. The code is
   built so that the answer "no" costs an adapter, not a product (audit §4).

2. **The CSS selectors are configuration, not code, and they are unverified.**
   They live in ``selectors.json`` and every one of them is marked
   ``"verified": false``. They are a starting point derived from conventional
   marketplace markup — not from an inspection of the live site — and they will
   need correcting against the real pages.

   The alternative would have been to write selectors into this file and
   present them as working. That would have been a guess dressed as a fact, and
   the first person to run it would have discovered the problem in production
   rather than here.

   ``python -m app.adapters.market.verify_turbo <url>`` fetches one page and
   reports which selectors matched, so correcting them is a ten-minute job
   against a live page rather than an archaeology exercise.

Because selectors are unverified, per-field extraction reporting is not a nicety
here — it is the mechanism by which a wrong selector announces itself.
"""

from __future__ import annotations

import json
import pathlib
import re
from collections.abc import AsyncIterator
from datetime import datetime
from urllib.parse import urljoin

from app.adapters.market.base import ParseResult, RawListing
from app.adapters.market.http import PoliteHttpClient, RobotsDenied
from app.domain.enums import Currency, ListingStatus, SellerType
from app.domain.identity import VehicleConfiguration
from app.domain.money import Money
from app.domain.normalization import normalize_city, normalize_seller_type

SELECTORS_PATH = pathlib.Path(__file__).parent / "selectors.json"

#: Digits, optionally with separators. Prices and mileages in listings are
#: written as "43 500", "43,500" or "43500" depending on the page.
_NUMBER = re.compile(r"\d[\d\s,.]*")

_CURRENCY_TOKENS = {
    "azn": Currency.AZN,
    "₼": Currency.AZN,
    "man": Currency.AZN,
    "usd": Currency.USD,
    "$": Currency.USD,
    "eur": Currency.EUR,
    "€": Currency.EUR,
}


def load_selectors(path: pathlib.Path = SELECTORS_PATH) -> dict[str, dict]:
    if not path.exists():
        raise FileNotFoundError(f"selector configuration missing at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class TurboAdapter:
    """Incremental adapter for Turbo.az.

    Fetching is delegated entirely to :class:`PoliteHttpClient`, so robots.txt
    compliance and rate limiting cannot be bypassed from here.
    """

    slug = "turbo.az"
    display_name = "Turbo.az"

    def __init__(
        self,
        client: PoliteHttpClient,
        base_url: str = "https://turbo.az",
        selectors: dict[str, dict] | None = None,
        max_pages: int = 50,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._selectors = selectors or load_selectors()
        self._max_pages = max_pages

    @property
    def selectors_verified(self) -> bool:
        """Whether every selector has been confirmed against the live site.

        The ingestion service refuses to run against production while this is
        false, so an unverified parser cannot quietly fill the market database
        with nulls.
        """
        listing = self._selectors.get("listing", {})
        return bool(listing) and all(
            spec.get("verified") is True for spec in listing.values() if isinstance(spec, dict)
        )

    async def discover(self, since: datetime | None = None) -> AsyncIterator[str]:
        """Yield listing detail URLs from index pages.

        Incremental by default: paging stops as soon as a page yields nothing
        new, because the index is ordered newest-first. A full enumeration is
        the initial backfill only, and is bounded by ``max_pages`` regardless.
        """
        index_template = self._selectors.get("index", {}).get("url_template")
        if not index_template:
            raise RuntimeError("selectors.json has no index.url_template")

        seen: set[str] = set()
        for page in range(1, self._max_pages + 1):
            url = urljoin(self._base_url + "/", index_template.format(page=page))
            try:
                result = await self._client.get(url)
            except RobotsDenied:
                return
            if not result.ok or not result.text:
                return

            links = self._extract_links(result.text)
            fresh = [link for link in links if link not in seen]
            if not fresh:
                return
            seen.update(fresh)
            for link in fresh:
                yield link

    async def fetch(self, identifier: str) -> ParseResult:
        url = identifier if identifier.startswith("http") else urljoin(self._base_url, identifier)
        try:
            result = await self._client.get(url)
        except RobotsDenied as exc:
            return ParseResult(listing=None, errors=(str(exc),))

        if result.unchanged:
            return ParseResult(listing=None, errors=("unchanged since last fetch",))
        if not result.ok:
            return ParseResult(listing=None, errors=(f"HTTP {result.status_code}",))

        return self.parse(result.text, url, datetime.now(tz=_utc()))

    # --- parsing -----------------------------------------------------------

    def parse(self, html: str, url: str, observed_at: datetime) -> ParseResult:
        """Extract a listing from a detail page.

        Returns per-field success even when the overall parse succeeds. A
        listing that parsed but lost its mileage is a partial failure, and the
        aggregate of those is what reveals a markup change.
        """
        spec = self._selectors.get("listing", {})
        missing: list[str] = []
        errors: list[str] = []
        raw: dict[str, str] = {}

        def grab(field_name: str) -> str | None:
            value = _extract(html, spec.get(field_name, {}))
            if value is None:
                missing.append(field_name)
                return None
            raw[field_name] = value
            return value

        external_id = grab("external_id") or _id_from_url(url)
        if not external_id:
            return ParseResult(
                listing=None,
                missing_fields=tuple(missing),
                errors=("could not determine a listing identifier",),
            )

        price_text = grab("price")
        price = _parse_money(price_text) if price_text else None
        if price is None:
            errors.append("price could not be parsed")
            return ParseResult(
                listing=None, missing_fields=tuple(missing), errors=tuple(errors)
            )

        make_text = grab("make")
        model_text = grab("model")
        year_text = grab("model_year")
        mileage_text = grab("mileage_km")
        city_text = grab("city")
        fuel_text = grab("fuel")
        transmission_text = grab("transmission")
        drivetrain_text = grab("drivetrain")
        body_text = grab("body")
        engine_text = grab("engine")
        seller_text = grab("seller_type")
        condition_text = grab("condition")
        description = _extract(html, spec.get("description", {}))

        configuration = VehicleConfiguration.from_raw(
            make=make_text,
            model=model_text,
            model_year=_parse_int(year_text),
            trim=raw.get("trim"),
            displacement=_parse_displacement(engine_text),
            fuel=fuel_text,
            transmission=transmission_text,
            drivetrain=drivetrain_text,
            body=body_text,
        )

        unmapped: dict[str, str] = {}
        if make_text and configuration.make is None:
            unmapped["make"] = make_text
        if city_text and normalize_city(city_text) is None:
            unmapped["city"] = city_text

        listing = RawListing(
            external_id=external_id,
            source_url=url,
            configuration=configuration,
            price=price,
            observed_at=observed_at,
            mileage_km=_parse_int(mileage_text),
            city=normalize_city(city_text),
            seller_type=(
                normalize_seller_type(seller_text) if seller_text else SellerType.UNKNOWN
            ),
            status=ListingStatus.ACTIVE,
            # The condition field is the seller stating this in a structured
            # box; the description is the same claim buried in prose. Read the
            # box first and fall back to the prose.
            has_damage_disclosure=_disclosure(condition_text, description, "damage"),
            has_repaint_disclosure=_disclosure(condition_text, description, "repaint"),
            description=description,
            raw_fields=raw,
        )
        return ParseResult(
            listing=listing,
            missing_fields=tuple(missing),
            errors=tuple(errors),
            unmapped_values=unmapped,
        )

    def _extract_links(self, html: str) -> list[str]:
        pattern = self._selectors.get("index", {}).get("link_pattern")
        if not pattern:
            return []
        return [
            urljoin(self._base_url, match)
            for match in dict.fromkeys(re.findall(pattern, html))
        ]

    async def close(self) -> None:
        await self._client.close()


# --- extraction helpers ----------------------------------------------------


def _extract(html: str, spec: dict) -> str | None:
    """Apply one selector specification to a page.

    Regex-based rather than DOM-based, which keeps the adapter dependency-free
    and makes each rule self-contained and independently correctable. A single
    wrong rule breaks one field, not the whole parse.
    """
    pattern = spec.get("pattern")
    if not pattern:
        return None
    match = re.search(pattern, html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    group = spec.get("group", 1)
    try:
        value = match.group(group)
    except (IndexError, re.error):
        return None
    if value is None:
        return None
    cleaned = re.sub(r"<[^>]+>", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_money(text: str) -> Money | None:
    """Parse a price with its currency.

    Currency is read from the text rather than assumed. A USD price silently
    treated as AZN would be roughly a 40% error, and would poison every
    comparable set it landed in.
    """
    match = _NUMBER.search(text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(0))
    if not digits:
        return None

    currency = Currency.AZN
    lowered = text.lower()
    for token, candidate in _CURRENCY_TOKENS.items():
        if token in lowered:
            currency = candidate
            break

    try:
        return Money.of(int(digits), currency)
    except ValueError:
        return None


def _parse_int(text: str | None) -> int | None:
    if not text:
        return None
    match = _NUMBER.search(text)
    if not match:
        return None
    digits = re.sub(r"[^\d]", "", match.group(0))
    return int(digits) if digits else None


def _parse_displacement(text: str | None) -> float | None:
    """Pull engine displacement out of text like ``2.0 L`` or ``1998 sm3``."""
    if not text:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def _id_from_url(url: str) -> str | None:
    """Fall back to the numeric identifier in the URL path."""
    match = re.search(r"/(\d{4,})", url)
    return match.group(1) if match else None


def _utc():  # type: ignore[no-untyped-def]
    from datetime import UTC

    return UTC


def _disclosure(condition: str | None, description: str | None, kind: str) -> bool | None:
    """Read one disclosure, preferring the structured field to the prose.

    Tri-state throughout: "the seller says it was never hit" and "the seller
    does not mention it" are different facts and stay different.
    """
    from app.engines.risk.signals import read_disclosures

    for text in (condition, description):
        if not text:
            continue
        reading = read_disclosures(text)
        value = reading.damage if kind == "damage" else reading.repaint
        if value is not None:
            return value
    return None
