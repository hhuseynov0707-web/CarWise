# Deployment

The repository ships a development environment. `docker-compose.yml` mounts the
source into both containers and runs them in watch mode, which is right for a
laptop and wrong for anything reachable from the internet. Deployment is that
file plus `docker-compose.prod.yml`, which replaces those parts.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

## What the overlay changes, and why

| | Development | Production |
|---|---|---|
| API source | mounted from the host | baked into the image |
| API server | `--reload`, one worker | no reloader, `API_WORKERS` workers |
| Web | `next dev` | `next build` → `next start`, standalone output |
| Postgres / Redis ports | published to the host | not published |
| Restart | none | `unless-stopped` |
| Ingestion | whatever `.env` says | off unless explicitly enabled |

Two of those are worth stating plainly. A deployment that used the base file
alone would serve whatever happened to be on the host's disk through a
reloader. And the database ports are published in development so a `psql` on
the host can reach them; on a public machine that is a database on the
internet.

## Before the first deploy

1. **Copy the template.** `.env.production.example` → `.env` on the host. Every
   variable it lists without a default is one the application will refuse to
   start without, which is deliberate — a missing secret should stop a
   deployment, not silently weaken it.

2. **Generate a secret.**

   ```bash
   openssl rand -hex 32
   ```

   `SECRET_KEY` is required at 32+ characters outside local. The application
   checks this at startup and exits rather than running without it.

3. **Set `CORS_ORIGINS` to the site's own origin and nothing else.** The
   session is a cookie and the API sends `Access-Control-Allow-Credentials`. A
   wildcard would let any page on the internet make authenticated requests with
   a visitor's session.

4. **Point `NEXT_PUBLIC_API_URL` at the public API URL.** Next inlines
   `NEXT_PUBLIC_*` into the client bundle at build time, so this cannot be
   changed by restarting the container — it needs a rebuild.

5. **Terminate TLS in front of the stack.** Neither container does. The session
   cookie is marked `Secure` outside local, which means a browser will not send
   it over plain HTTP and nobody will be able to stay signed in.

## Running the migrations

The API container runs `alembic upgrade head` before starting, so a deploy
migrates on the way up. That is convenient with one instance and wrong with
several — concurrent migrations on the same database race. With more than one
API replica, drop the migration from the command and run it once as a separate
step.

## Ingestion

Off by default here, and that is a decision rather than an oversight. Crawling
from a laptop for personal use and crawling continuously from a public service
are different acts, and the terms review recorded in
`00-architecture-audit.md` §4 was made about the first.

If it is enabled, the crawl rate in the template is lower than the development
default. A deployment runs unattended.

## What is not covered

- **Backups.** `postgres_data` is a Docker volume. Nothing here backs it up,
  and the market history is the expensive thing to lose — it took days of
  polite crawling to build and cannot be re-fetched retrospectively.
- **Logs and monitoring.** The API logs to stdout; nothing collects it.
- **Search engines.** The web app sends `robots: noindex`. Deliberate while the
  data is being validated; remove it when the site is meant to be found.

## What the site publishes

Audit §4.7 governs this and the deployment does not change it: the interface
shows structured facts and a link back to the source listing, never the
seller's photographs or their prose as the site's own content. Descriptions are
held for analysis — disclosure detection, risk signals, and as context the
expert may read but is instructed not to quote.

Worth re-reading before the site is made public, because that is the point at
which the rule stops being theoretical.
