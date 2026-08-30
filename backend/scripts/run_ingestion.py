"""Run one Turbo.az ingestion pass from the command line.

    python scripts/run_ingestion.py --max-listings 50 --max-pages 3

The HTTP route (``POST /api/v1/admin/ingestion/run``) is the operational entry
point and the one n8n calls. This exists for the initial backfill, where a long
run wants a terminal, a progress line and a summary rather than a request that
sits open for hours.

Every guard still applies. The service refuses to run while ingestion is
disabled or while any extraction rule is unverified, the crawl obeys robots.txt
and the configured rate limit, and a run aborts on its own if per-field
extraction rates collapse.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.market.http import PoliteHttpClient  # noqa: E402
from app.adapters.market.turbo import TurboAdapter  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.domain.money import FxTable  # noqa: E402
from app.eventloop import use_selector_event_loop_on_windows  # noqa: E402
from app.services.ingestion import IngestionService  # noqa: E402


async def _run(max_listings: int, max_pages: int, commit_every: int) -> int:
    settings = get_settings()

    if not settings.ingestion_enabled:
        print("ingestion is disabled; set INGESTION_ENABLED=true to run", file=sys.stderr)
        return 2

    client = PoliteHttpClient(
        user_agent=settings.crawl_user_agent,
        requests_per_second=settings.crawl_requests_per_second,
        burst=settings.crawl_burst,
        timeout_seconds=settings.crawl_timeout_seconds,
        robots_cache_seconds=settings.robots_cache_seconds,
    )
    adapter = TurboAdapter(client=client, max_pages=max_pages)

    if not adapter.selectors_verified:
        print("extraction rules are not marked verified; run verify_turbo first", file=sys.stderr)
        return 2

    print(
        f"source={adapter.slug}  max_listings={max_listings}  max_pages={max_pages}  "
        f"rate={settings.crawl_requests_per_second}/s",
        flush=True,
    )

    database = Database(url=settings.database_url)
    try:
        async with database.session() as session:
            service = IngestionService(
                session=session,
                fx=FxTable(),
                ingestion_enabled=True,
                commit_every=commit_every,
            )
            report = await service.run(
                adapter, as_of=datetime.now(UTC), max_listings=max_listings
            )
    finally:
        await adapter.close()
        await database.dispose()

    print()
    print(f"status           : {report.status}")
    print(f"seen             : {report.listings_seen}")
    print(f"created          : {report.listings_created}")
    print(f"updated          : {report.listings_updated}")
    print(f"unchanged        : {report.listings_unchanged}")
    print(f"price changes    : {report.price_changes}")
    print(f"errors           : {report.errors}")
    print(f"quality issues   : {report.quality_issues}")

    rates = report.health.rates()
    if rates:
        print("per-field extraction rates:")
        for name in sorted(rates):
            flag = "  <-- degraded" if rates[name] < 0.95 else ""
            print(f"  {name:<16} {rates[name]:.0%}{flag}")

    unmapped = report.unmapped_summary()
    if unmapped:
        print("values our tables did not recognise:")
        for name, values in sorted(unmapped.items()):
            print(f"  {name:<16} {', '.join(values[:10])}")

    if report.abort_reason:
        print(f"\nABORTED: {report.abort_reason}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-listings", type=int, default=50)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--commit-every",
        type=int,
        default=25,
        help="checkpoint every N listings so a long run is resumable",
    )
    args = parser.parse_args()

    use_selector_event_loop_on_windows()
    return asyncio.run(_run(args.max_listings, args.max_pages, args.commit_every))


if __name__ == "__main__":
    raise SystemExit(main())
