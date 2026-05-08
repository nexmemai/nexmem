# Deployment Guide

## Prerequisites
- GitHub account
- Render account (free tier works)
- Supabase account for PostgreSQL + pgvector

## Backend Deployment (Render)

### 1. Prepare Repository
```bash
git push your changes to GitHub
```

### 2. Create Render Account
1. Go to https://render.com
2. Sign up with GitHub
3. Connect your repository

### 3. Deploy Backend
1. Click **New** → **Web Service**
2. Connect your GitHub repo
3. Configure:
   - **Name**: `ai-memory-api`
   - **Region**: Choose closest to users
   - **Branch**: `main`
   - **Runtime**: Docker
   - **Plan**: Free

4. Set environment variables in the Render dashboard. Do not commit real values
   to `render.yaml`, scripts, docs, or `.env` files:
   ```
   DATABASE_URL=<Supabase pooler URL stored as a Render secret>
   OPENAI_API_KEY=<stored as a Render secret>
   SECRET_KEY=<stored as a Render secret; generate with secrets.token_hex(32)>
   ENVIRONMENT=production
   ALLOWED_ORIGINS=https://your-frontend.example
   ```

5. Set health check path: `/health/live`

6. Click **Deploy**

### 4. Create PostgreSQL Database
1. Click **New** → **PostgreSQL**
2. Choose free tier
3. Copy the **Internal Connection String**
4. Add to Backend environment variables

### 5. Run Initial Migration
On Render, migrations run through `releaseCommand: "alembic upgrade head"` in
`render.yaml`. The web start command should only start the API process.
Do not add Alembic to the Dockerfile `CMD` or any web process startup command.

To run manually against a non-production database:
```bash
export DATABASE_URL='<non-production database URL>'
alembic upgrade head
```

For local Docker production-style testing, run migrations explicitly before the
API starts:
```bash
docker compose -f docker-compose.prod.yml --profile migrate run --rm migrate
docker compose -f docker-compose.prod.yml up --build api
```

### 6. Seed Demo Data
```bash
# Set DATABASE_URL in your shell first. Never paste production URLs into scripts.
python scripts/seed_demo.py
```

## Frontend Deployment (Streamlit Cloud)

The repo's Vercel workflow for `nexmem-landing/` is preview-first. Automated
pushes should create Vercel preview deployments only. Promote to production from
Vercel after reviewing the preview URL.

### 1. Update Streamlit Secrets
Create `.streamlit/secrets.toml`:
```toml
API_BASE_URL = "https://your-backend-url.onrender.com"
```

### 2. Deploy to Streamlit Cloud
1. Go to https://streamlit.io/cloud
2. Connect your GitHub repo
3. Select frontend folder
4. Deploy

## Docker Compose (Full Stack)

For local production testing:
```bash
# Copy local operator env; do not commit it
cp .env.production .env

# Fill in your values
# DATABASE_URL, OPENAI_API_KEY, SECRET_KEY, ALLOWED_ORIGINS

# Deploy
docker-compose -f docker-compose.prod.yml up --build
```

## Verify Deployment

### Backend Health
```bash
curl https://your-api.onrender.com/health/ready
```

### Run Demo Walkthrough
1. Register a user
2. Create an API key
3. Query `/memory/context`
4. Write an episode with `/memory/episode/write`

## Troubleshooting

### Database Connection Issues
- Check DATABASE_URL format
- Verify PostgreSQL is accessible
- Check pgvector extension is enabled

### API Key Issues
```bash
# Regenerate keys
python scripts/seed_demo.py
```

### 503 on /health/ready
- Check DATABASE_URL is set
- Verify PostgreSQL is running
- Check OpenAI API key format
