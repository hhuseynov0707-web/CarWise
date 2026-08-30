"""Compute market snapshots from the listings already stored.

    python backend/scripts/build_snapshots.py

Snapshots are per-configuration aggregates over a time window — median, the
percentile spread, dispersion, sample size. They are what lets a screen ask
"which listings sit below their own market?" without running a full valuation
for every configuration on every request.

The HTTP route (``POST /api/v1/admin/snapshots/build``) is the operational
entry point and the one n8n calls; this exists for the same reason the
ingestion runner does, so a long first build has a terminal and a summary.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.db.session import Database  # noqa: E402
from app.eventloop import use_selector_event_loop_on_windows  # noqa: E402
from app.services.snapshots import SnapshotService  # noqa: E402


async def _run() -> int:
    settings = get_settings()
    database = Database(url=settings.database_url)
    try:
        async with database.session() as session:
            report = await SnapshotService(session=session).build(datetime.now(UTC))
    finally:
        await database.dispose()

    print(f"scopes considered : {report.scopes_considered}")
    print(f"snapshots written : {report.snapshots_written}")
    print(f"skipped as thin   : {report.skipped_thin}")
    return 0


def main() -> int:
    use_selector_event_loop_on_windows()
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
