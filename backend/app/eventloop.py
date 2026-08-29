"""Event loop selection for platforms whose default loop the driver rejects.

Windows defaults to :class:`asyncio.ProactorEventLoop`, and psycopg's async mode
cannot run on it — every connection attempt fails with ``Psycopg cannot use the
'ProactorEventLoop' to run in async mode``. The selector loop works, so any
entry point that opens a database connection has to get one.

There are two ways in, because the two entry points differ:

* The migration runner calls :func:`asyncio.run` itself, so setting the process
  event loop policy before that call is enough.
* Uvicorn 0.36+ does not consult the policy at all. It builds the loop from a
  factory, and on Windows that factory hands back a proactor loop unless it is
  told otherwise. :func:`loop_factory` is that override, passed as
  ``--loop app.eventloop:loop_factory``.

On Linux and macOS both are no-ops in effect — the selector loop is already the
default — so the same commands work everywhere.
"""

from __future__ import annotations

import asyncio
import sys


def use_selector_event_loop_on_windows() -> bool:
    """Install the selector event loop policy when running on Windows.

    For entry points that call :func:`asyncio.run` themselves. Must run before
    the loop is created; afterwards the policy is fixed and this does nothing.
    Returns whether the policy was changed.

    This does **not** affect uvicorn, which bypasses the policy entirely — use
    :func:`loop_factory` for that.
    """
    if sys.platform != "win32":
        return False

    policy_cls = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_cls is None:  # pragma: no cover - non-Windows or a future removal
        return False

    if isinstance(asyncio.get_event_loop_policy(), policy_cls):
        return False

    asyncio.set_event_loop_policy(policy_cls())
    return True


def loop_factory() -> asyncio.AbstractEventLoop:
    """Build the event loop the database driver can actually run on.

    Passed to uvicorn as ``--loop app.eventloop:loop_factory``. Uvicorn imports
    this and calls it with no arguments, so the signature is fixed by that
    contract.

    The selector loop is already the default on Linux and macOS; naming it
    explicitly costs nothing there and keeps one launch command for every
    platform.
    """
    return asyncio.SelectorEventLoop()
