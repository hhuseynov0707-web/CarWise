"""Re-resolve stored listings against the current normalisation rules.

    python backend/scripts/replay_configurations.py --dry-run
    python backend/scripts/replay_configurations.py

``Listing.raw_payload`` keeps the source's own strings so that a parser or
normalisation fix can be replayed against historical rows without re-crawling.
This is that replay.

It is needed rather than optional because identity is frozen at insert.
``IngestionService._upsert`` writes ``config_id`` and the identity ladder only
on the INSERT branch: re-crawling a listing that already exists refreshes its
price, mileage and description, and leaves its configuration exactly as first
resolved. A normalisation fix therefore reaches new listings only.

And ``config_id`` is a content hash that includes the body style, so correcting
a body moves a listing to a *different* configuration. That makes this a
re-resolution — register the configuration if it is new, then repoint the
listing — not an UPDATE of one column.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

# Private helpers, deliberately: the payload was written by this adapter and
# has to be read back with the same parsing it was written with.
from app.adapters.market.turbo import _parse_displacement, _parse_int  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.models import Listing, VehicleConfigurationRow  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.domain.identity import VehicleConfiguration  # noqa: E402
from app.eventloop import use_selector_event_loop_on_windows  # noqa: E402


def _configuration_from(payload: dict) -> VehicleConfiguration:
    """Rebuild a configuration the way the adapter builds one at parse time."""
    engine = payload.get("engine")
    return VehicleConfiguration.from_raw(
        make=payload.get("make"),
        model=payload.get("model"),
        model_year=_parse_int(payload.get("model_year")),
        trim=payload.get("trim"),
        displacement=_parse_displacement(engine),
        fuel=payload.get("fuel"),
        transmission=payload.get("transmission"),
        drivetrain=payload.get("drivetrain"),
        body=payload.get("body"),
    )


async def _run(dry_run: bool, batch: int) -> int:
    settings = get_settings()
    database = Database(url=settings.database_url)

    changed = unchanged = skipped = 0
    bodies: Counter[str] = Counter()
    new_configs = 0

    try:
        async with database.session() as session:
            listings = (await session.scalars(select(Listing))).unique().all()
            print(f"{len(listings)} listings to replay", flush=True)

            known = set(
                (await session.scalars(select(VehicleConfigurationRow.config_id))).all()
            )

            for index, listing in enumerate(listings, start=1):
                payload = listing.raw_payload or {}
                if not payload:
                    skipped += 1
                    continue

                config = _configuration_from(payload)
                if not config.is_resolvable:
                    skipped += 1
                    continue

                if config.config_id == listing.config_id:
                    unchanged += 1
                    continue

                changed += 1
                bodies[f"{payload.get('body')} -> {config.body.value}"] += 1

                if dry_run:
                    continue

                if config.config_id not in known:
                    session.add(
                        VehicleConfigurationRow(
                            config_id=config.config_id,
                            model_key=config.model_key,
                            generation_key=config.generation_key,
                            powertrain_key=config.powertrain_key,
                            canonical_string=config.canonical_string,
                            make=config.make,
                            model=config.model,
                            model_year=config.model_year,
                            generation=config.generation,
                            trim=config.trim,
                            engine_code=config.engine_code,
                            displacement_l=config.displacement_l,
                            fuel=config.fuel.value,
                            transmission=config.transmission.value,
                            drivetrain=config.drivetrain.value,
                            body=config.body.value,
                            horsepower=config.horsepower,
                            import_status=config.import_status.value,
                            specificity=config.specificity,
                        )
                    )
                    known.add(config.config_id)
                    new_configs += 1
                    await session.flush()

                listing.config_id = config.config_id
                listing.model_key = config.model_key
                listing.generation_key = config.generation_key
                listing.powertrain_key = config.powertrain_key

                if index % batch == 0:
                    await session.commit()
                    print(f"  ... {index}/{len(listings)}", flush=True)
    finally:
        await database.dispose()

    print()
    print(f"{'would change' if dry_run else 'changed'} : {changed}")
    print(f"unchanged     : {unchanged}")
    print(f"skipped       : {skipped}")
    if not dry_run:
        print(f"new configs   : {new_configs}")
    if bodies:
        print("body corrections:")
        for label, count in bodies.most_common(20):
            print(f"  {count:>6}  {label}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--batch", type=int, default=200, help="commit every N listings")
    args = parser.parse_args()

    use_selector_event_loop_on_windows()
    return asyncio.run(_run(args.dry_run, args.batch))


if __name__ == "__main__":
    raise SystemExit(main())
