# Bringing up a server

`01-deployment.md` explains what the production overlay changes and why. This
is the sequence to run on a fresh host, in order.

Everything below assumes Ubuntu 22.04 or 24.04 and a user with `sudo`. Replace
`SERVER_IP` with the host's address throughout.

## 1. Docker

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and back in, or the group change does not apply to the current shell.
Check with `docker run --rm hello-world` before continuing — every later step
assumes it works.

## 2. The firewall

Only Caddy is reachable from outside; the application and database ports are
not published at all by the production overlay. What has to be open is 80 and
443, and 80 is not optional: Let's Encrypt validates over it.

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

## 3. The code

```bash
git clone https://github.com/hhuseynov0707-web/CarWise.git
cd CarWise
cp .env.production.example .env
```

## 4. Fill in `.env`

Four values matter before anything will start. The overlay refuses to
interpolate without them, which is deliberate — a missing secret should stop a
deployment rather than quietly weaken it.

```bash
openssl rand -hex 32          # paste as SECRET_KEY
openssl rand -hex 16          # paste as POSTGRES_PASSWORD
```

Then edit `.env`:

| Variable | Value |
|---|---|
| `SECRET_KEY` | the 32-byte hex above |
| `POSTGRES_PASSWORD` | the 16-byte hex above |
| `DATABASE_URL` | `postgresql+psycopg://autointel:<that password>@postgres:5432/autointel` |
| `SITE_ADDRESS` | `SERVER_IP.sslip.io` — see below |
| `CORS_ORIGINS` | `["https://SERVER_IP.sslip.io"]` |

`sslip.io` resolves the address embedded in the hostname, so
`203.0.113.10.sslip.io` resolves to `203.0.113.10`. That gives Let's Encrypt a
hostname it will issue a certificate for; it refuses bare IP addresses. Without
a certificate nobody can sign in, because the session cookie is `Secure`
outside local and a browser will not send it over plain HTTP.

Leave `NEXT_PUBLIC_API_URL` as `/api/v1`. Caddy serves the API under the site's
own origin, so a relative path resolves correctly and the built image is not
tied to this hostname — which is what makes step 8 a DNS record rather than a
rebuild.

## 5. Move the database across

The market history is the expensive thing here: it was gathered over days of
deliberately slow crawling and cannot be re-fetched retrospectively. Starting
empty means an empty site until a new crawl catches up.

From the machine holding the data:

```bash
scp carwise-data.dump USER@SERVER_IP:~/
```

The dump carries the schema, so restore it **before** the API starts. The API
runs `alembic upgrade head` on boot; restoring first means alembic finds
`alembic_version` already at head and does nothing, while doing it the other
way round has the migration and the dump both trying to create the same tables.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d postgres
docker compose exec -T postgres pg_isready -U autointel     # wait for ready

docker compose exec -T postgres pg_restore \
  -U autointel -d autointel --no-owner --no-privileges \
  < ~/carwise-data.dump
```

Sessions are deliberately absent from the dump — they are digests of cookies
that only exist in one browser on one host, so carrying them over would move
nothing but dead rows. Everyone signs in again.

## 6. Pin the overlay, then start

Every command so far has named both files. Forgetting the second one is worse
than a typo: plain `docker compose up -d` is *valid*, and it silently brings up
the development configuration — Postgres and Redis published to the internet,
the API on a reloader with the source mounted, and both app ports answering
directly with no TLS in front. A production host is one forgotten flag away
from an exposed database.

Compose reads `COMPOSE_FILE` from `.env`, so put it there once and every later
command in this directory picks up both files on its own:

```bash
echo "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml" >> .env
```

The separator is `:` on Linux and `;` on Windows. `docker compose config
--services` should now list five services including `caddy`; if `caddy` is
missing, the overlay is not being read and nothing below will work.

The application ignores the variable — its settings are configured with
`extra="ignore"` — so sharing `.env` between Compose and the container is safe.

```bash
docker compose up -d --build
```

The first start is slow: two images build, and Caddy then negotiates a
certificate. Watch it happen rather than guessing:

```bash
docker compose logs -f caddy
```

`certificate obtained successfully` is the line to wait for. If it does not
appear, the cause is almost always port 80 unreachable from the internet —
check the firewall and the provider's own security group, which is a separate
thing from `ufw`.

## 7. Check

```bash
curl -sS https://SERVER_IP.sslip.io/api/v1/health
curl -sS -o /dev/null -w '%{http_code}\n' https://SERVER_IP.sslip.io/
```

Health should report `"database":"ok"`. Then open the site and confirm
`Bu günün tapıntıları` lists vehicles — that exercises the browser, Caddy, the
API and the restored data in one action, which no single curl does.

## 8. Later: a real domain

Point an A record at the server, then change two values in `.env`:

```
SITE_ADDRESS=example.az
CORS_ORIGINS=["https://example.az"]
```

```bash
docker compose up -d caddy api
```

No rebuild. The frontend addresses the API by a relative path, so it follows
whatever hostname served it. Caddy obtains the new certificate on first
request.

## What this does not cover

- **Backups.** `postgres_data` is a Docker volume and nothing here backs it up.
  The same `pg_dump` used in step 5 is the tool; the decision to schedule it is
  the part that is missing.
- **Ingestion.** Off by default (`INGESTION_ENABLED=false`). Crawling
  continuously from a public service is a different act from crawling from a
  laptop, and the terms review in `00-architecture-audit.md` §4 was made about
  the second. Turning it on is a decision to make deliberately.
- **`robots: noindex`.** The web app still sends it. Remove it when the site is
  meant to be found.
