"""Polite HTTP client for market ingestion.

Every constraint from audit §4 lives here rather than in the adapters, because a
rule enforced by caller discipline is a rule that a future caller will
accidentally skip. Rate limiting is a property of the client; robots.txt is
checked inside :meth:`get`; there is no bypass parameter.

What this client does:

* fetches, parses, caches and obeys ``robots.txt`` — a disallowed path is never
  requested
* honours ``Crawl-delay`` when the origin declares one, and never crawls faster
  than the configured budget when it does not
* limits request rate with a token bucket, with jitter to avoid a metronomic
  pattern
* honours ``Retry-After`` on 429 and 503 rather than retrying blindly
* identifies itself with a real User-Agent and a contact URL
* makes conditional requests with ``ETag`` / ``If-Modified-Since``

What it deliberately does not do: rotate user agents, use proxies, solve
challenges, or ignore a disallow rule under any circumstance.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx


class RobotsDenied(RuntimeError):
    """The origin's robots.txt disallows this path for our user agent."""


class FetchFailed(RuntimeError):
    """A request failed after exhausting its retry budget."""


@dataclass
class TokenBucket:
    """Rate limiter with a small burst allowance.

    Sustained rate is what protects the origin; a small burst keeps latency
    reasonable when a run starts. Both are deliberately low — the incremental
    ingestion strategy means volume is not needed (audit §4.4).
    """

    rate_per_second: float
    capacity: int = 3
    _tokens: float = field(init=False)
    _last: float = field(init=False)

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._tokens = float(self.capacity)
        self._last = time.monotonic()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            self._tokens = min(
                float(self.capacity), self._tokens + (now - self._last) * self.rate_per_second
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            deficit = (1.0 - self._tokens) / self.rate_per_second
            # Jitter so concurrent workers do not synchronize into a pulse.
            await asyncio.sleep(deficit + random.uniform(0.0, 0.25))


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of one fetch."""

    url: str
    status_code: int
    text: str
    etag: str | None = None
    last_modified: str | None = None
    from_cache: bool = False
    """True on a 304, meaning the resource is unchanged since our last fetch."""

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def unchanged(self) -> bool:
        return self.status_code == 304


@dataclass
class _RobotsEntry:
    parser: RobotFileParser | None
    fetched_at: float
    crawl_delay: float | None
    reachable: bool


class PoliteHttpClient:
    """An HTTP client that cannot be made impolite by its callers."""

    def __init__(
        self,
        user_agent: str,
        requests_per_second: float = 0.2,
        burst: int = 3,
        timeout_seconds: float = 30.0,
        robots_cache_seconds: int = 3600,
        max_attempts: int = 3,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._bucket = TokenBucket(rate_per_second=requests_per_second, capacity=burst)
        self._robots_cache_seconds = robots_cache_seconds
        self._max_attempts = max_attempts
        self._robots: dict[str, _RobotsEntry] = {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "az,en;q=0.8,ru;q=0.6"},
        )
        self.requests_made = 0
        self.robots_denied = 0

    async def get(
        self,
        url: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchResult:
        """Fetch a URL, or raise :class:`RobotsDenied` if it is disallowed.

        Conditional headers are passed through when supplied; a 304 returns a
        result with ``unchanged`` set, which is how incremental ingestion avoids
        re-downloading pages that have not moved.
        """
        if not await self.allowed(url):
            self.robots_denied += 1
            raise RobotsDenied(f"robots.txt disallows {url} for {self._user_agent}")

        headers: dict[str, str] = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified

        delay = await self._crawl_delay(url)
        last_error: Exception | None = None

        for attempt in range(1, self._max_attempts + 1):
            await self._bucket.acquire()
            if delay:
                await asyncio.sleep(delay)

            try:
                response = await self._client.get(url, headers=headers)
                self.requests_made += 1
            except httpx.TimeoutException as exc:
                last_error = exc
                await self._backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                raise FetchFailed(f"{url}: {exc}") from exc

            if response.status_code in (429, 503):
                # Honour the origin's own instruction rather than guessing.
                wait = _retry_after_seconds(response) or _backoff_seconds(attempt)
                last_error = FetchFailed(f"{url}: {response.status_code}")
                if attempt == self._max_attempts:
                    break
                await asyncio.sleep(wait)
                continue

            if response.status_code >= 500:
                last_error = FetchFailed(f"{url}: {response.status_code}")
                if attempt == self._max_attempts:
                    break
                await self._backoff(attempt)
                continue

            return FetchResult(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text if response.status_code != 304 else "",
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
                from_cache=response.status_code == 304,
            )

        raise FetchFailed(f"{url} failed after {self._max_attempts} attempts: {last_error}")

    async def allowed(self, url: str) -> bool:
        """Whether robots.txt permits fetching this URL.

        An unreachable robots.txt is treated as **allow**, matching the common
        convention: a site with no robots file has expressed no restriction. A
        robots file that is present and disallows is always obeyed.
        """
        entry = await self._robots_for(url)
        if entry.parser is None:
            return True
        return entry.parser.can_fetch(self._user_agent, url)

    async def _crawl_delay(self, url: str) -> float | None:
        entry = await self._robots_for(url)
        return entry.crawl_delay

    async def _robots_for(self, url: str) -> _RobotsEntry:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        cached = self._robots.get(origin)
        now = time.monotonic()
        if cached and (now - cached.fetched_at) < self._robots_cache_seconds:
            return cached

        entry = await self._fetch_robots(origin)
        self._robots[origin] = entry
        return entry

    async def _fetch_robots(self, origin: str) -> _RobotsEntry:
        parser = RobotFileParser()
        try:
            response = await self._client.get(f"{origin}/robots.txt")
            self.requests_made += 1
        except httpx.HTTPError:
            return _RobotsEntry(None, time.monotonic(), None, reachable=False)

        if response.status_code >= 400:
            return _RobotsEntry(None, time.monotonic(), None, reachable=False)

        parser.parse(response.text.splitlines())
        delay = parser.crawl_delay(self._user_agent)
        return _RobotsEntry(
            parser=parser,
            fetched_at=time.monotonic(),
            crawl_delay=float(delay) if delay else None,
            reachable=True,
        )

    async def _backoff(self, attempt: int) -> None:
        await asyncio.sleep(_backoff_seconds(attempt))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> PoliteHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with jitter."""
    return min(60.0, (2.0**attempt)) + random.uniform(0.0, 1.0)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a ``Retry-After`` header expressed in seconds."""
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        # The HTTP-date form is also legal; we do not parse it, and fall back
        # to our own backoff rather than guessing.
        return None
