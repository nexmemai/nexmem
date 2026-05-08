# NexMem Deployment Guide: Render + Supabase

This is the production source of truth for deploying the NexMem API to Render with Supabase PostgreSQL.

## Secret Handling Rules

- `DATABASE_URL` must be provided only through environment variables or the Render dashboard.
- Never commit a real database hostname, username, password, pooler URL, or copied connection string.
- `alembic/env.py` intentionally fails if `DATABASE_URL` is missing.
- `render.yaml` must keep `DATABASE_URL` as `sync: false`.
- Local `.env.local` and `.env.production` files are operator-owned secrets and must stay out of git.

## Supabase Database URL

Use the Supabase connection pooler for Render. In Supabase Dashboard, go to **Settings -> Database -> Connection Pooler**.

Expected Render value format:

```text
DATABASE_URL=postgresql+asyncpg://<pooler-user>:<url-encoded-password>@<pooler-host>:6543/postgres?sslmode=require
```

Notes:
- Use transaction pooler mode.
- URL-encode special characters in the password.
- Do not paste this value into `render.yaml`, docs, scripts, tests, or commit messages.

## Render Configuration

`render.yaml` defines the service wiring. Set secret values in Render Dashboard under each service's **Environment** tab.

Required backend secrets:

```text
DATABASE_URL=<Supabase pooler URL>
OPENAI_API_KEY=<OpenAI API key>
SECRET_KEY=<64+ hex chars from secrets.token_hex(32)>
```

Required non-secret production values:

```text
DEMO_MODE=false
ENVIRONMENT=production
ALLOWED_ORIGINS=https://nexmem.vercel.app,https://nexmem-1.onrender.com
```

Render command separation:

```text
Build Command:   pip install --upgrade pip && pip install -r requirements.txt
Release Command: alembic upgrade head
Start Command:   uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The web start command must not run migrations. Migrations run once through Render's release command before the new service instance is promoted.

Production migration source of truth: `render.yaml` `releaseCommand`. Do not add
`alembic upgrade head` to `startCommand`, the root `Dockerfile` `CMD`, or the API
web command in Docker Compose. For local Docker migration checks, run the
migration command explicitly:

```bash
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrate
```

## Frontend Preview Deploys

The GitHub workflow for `nexmem-landing/` creates a Vercel preview deployment.
It intentionally does not pass `--prod`. Promote a reviewed preview to
production from Vercel after verifying the preview URL against the intended API
origin.

## Rotation Checklist

Complete these steps whenever a database URL or password has been exposed:

1. Rotate the Supabase database password in Supabase Dashboard.
2. Build a new `DATABASE_URL` using the rotated password and the Supabase pooler host.
3. Update `DATABASE_URL` in Render for `nexmem-api`.
4. Update `DATABASE_URL` in Render for `nexmem-celery-worker`.
5. Trigger a Render deploy and confirm the release command runs `alembic upgrade head`.
6. Confirm the web service starts with the `uvicorn` start command.
7. Run the Supabase verification queries below.
8. Review git history, issue trackers, build logs, chat transcripts, and copied reports for leaked connection strings. Rotate again if a real credential appears anywhere outside the secret manager.

## Verification

Local env-only migration check:

```bash
export DATABASE_URL='<non-production database URL>'
alembic upgrade head
```

Render dashboard checks:

- `DATABASE_URL` is present as a secret env var on `nexmem-api`.
- `DATABASE_URL` is present as a secret env var on `nexmem-celery-worker`.
- `render.yaml` shows `DATABASE_URL` with `sync: false`.
- Latest deploy logs show the release command running migrations before the service starts.
- Runtime logs show startup without printing the connection string.

Supabase checks after deploy:

```sql
SELECT now();

SELECT tablename, rowsecurity, forcerowsecurity
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY tablename;

SELECT version_num
FROM alembic_version;
```

Application checks:

```bash
curl -fsS https://<render-service>.onrender.com/health/live
curl -fsS https://<render-service>.onrender.com/health/ready
```

## Gitleaks Note

The repository now has CI secret scanning, but full-history scanning can still fail if old commits contain leaked credentials. If that happens, do not bypass the scan. Rotate the exposed secret, decide whether history rewrite is appropriate for the repository, and record the leak as remediated only after the old credential is invalid.
