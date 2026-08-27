"""Selector verification tool.

    python -m app.adapters.market.verify_turbo <listing-url>

Fetches **one** page — obeying robots.txt and the rate limit like any other
request — and reports, rule by rule, whether it matched and what it captured.

This exists because the alternative way to correct a scraper is to run the whole
pipeline, look at the nulls in the database, and guess. This turns that into a
loop measured in seconds, against a single page, with the captured value printed
next to the rule that produced it.

Also accepts ``--file`` to run against saved HTML, which is how the parser gets
regression-tested without touching the network at all.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import UTC, datetime

from app.adapters.market.http import PoliteHttpClient, RobotsDenied
from app.adapters.market.turbo import TurboAdapter, load_selectors

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_DIM = "\033[2m"
_RESET = "\033[0m"


async def _load_html(url: str | None, file: pathlib.Path | None, user_agent: str) -> str:
    if file:
        return file.read_text(encoding="utf-8", errors="replace")
    if not url:
        raise SystemExit("supply a URL or --file")

    async with PoliteHttpClient(user_agent=user_agent, requests_per_second=0.2) as client:
        try:
            result = await client.get(url)
        except RobotsDenied as exc:
            raise SystemExit(f"{_RED}refused: {exc}{_RESET}") from exc
        if not result.ok:
            raise SystemExit(f"{_RED}HTTP {result.status_code} for {url}{_RESET}")
        return result.text


def _report(adapter: TurboAdapter, html: str, url: str) -> int:
    """Print a rule-by-rule report. Returns a process exit code."""
    parsed = adapter.parse(html, url, datetime.now(UTC))
    selectors = load_selectors()["listing"]
    missing = set(parsed.missing_fields)

    print(f"\n{_DIM}page length: {len(html):,} characters{_RESET}")
    print(f"{_DIM}{'-' * 78}{_RESET}")
    print(f"{'FIELD':<16} {'STATUS':<10} CAPTURED")
    print(f"{_DIM}{'-' * 78}{_RESET}")

    matched = 0
    for name in selectors:
        verified = selectors[name].get("verified") is True
        if name in missing:
            status = f"{_RED}no match{_RESET}"
            captured = ""
        else:
            matched += 1
            status = f"{_GREEN}matched{_RESET}" if verified else f"{_YELLOW}matched?{_RESET}"
            captured = (parsed.listing.raw_fields.get(name, "") if parsed.listing else "")[:44]
        print(f"{name:<16} {status:<19} {captured}")

    print(f"{_DIM}{'-' * 78}{_RESET}")
    print(f"{matched}/{len(selectors)} rules matched")

    if parsed.errors:
        print(f"\n{_RED}errors:{_RESET}")
        for error in parsed.errors:
            print(f"  - {error}")

    if parsed.unmapped_values:
        print(f"\n{_YELLOW}values the normalization tables did not recognise:{_RESET}")
        for name, value in parsed.unmapped_values.items():
            print(f"  {name}: {value!r}  (add it to app/domain/normalization.py)")

    if parsed.listing:
        config = parsed.listing.configuration
        print(f"\n{_GREEN}resolved vehicle:{_RESET} {config.describe()}")
        print(f"  configuration id : {config.config_id}")
        print(f"  specificity      : {config.specificity:.0%}")
        print(f"  price            : {parsed.listing.price}")
        print(f"  mileage          : {parsed.listing.mileage_km}")
        print(f"  city             : {parsed.listing.city}")
    else:
        print(f"\n{_RED}the page did not parse into a usable listing{_RESET}")

    if not adapter.selectors_verified:
        print(
            f"\n{_YELLOW}selectors.json still contains unverified rules. Once the report "
            f"above is correct, set \"verified\": true on each rule — ingestion refuses "
            f"to run until then.{_RESET}"
        )

    return 0 if parsed.listing and not missing else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="verify_turbo",
        description="Check Turbo.az extraction rules against a real or saved page.",
    )
    parser.add_argument("url", nargs="?", help="listing URL to fetch")
    parser.add_argument("--file", type=pathlib.Path, help="parse saved HTML instead of fetching")
    parser.add_argument(
        "--user-agent",
        default="AutoIntelBot/0.1 (+https://autointel.az/bot)",
        help="identify the crawler honestly",
    )
    args = parser.parse_args(argv)

    html = asyncio.run(_load_html(args.url, args.file, args.user_agent))
    adapter = TurboAdapter(client=None, selectors=load_selectors())  # type: ignore[arg-type]
    return _report(adapter, html, args.url or str(args.file))


if __name__ == "__main__":
    sys.exit(main())
