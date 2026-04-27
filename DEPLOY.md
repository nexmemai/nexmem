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

4. Set environment variables:
   ```
   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db
   OPENAI_API_KEY=sk-...
   SECRET_KEY=<generate-with: python -c "import secrets; print(secrets.token_hex(32))">
   ENVIRONMENT=production
   ALLOWED_ORIGINS=https://your-frontend-url.onrender.com
   ```

5. Set health check path: `/health/live`

6. Click **Deploy**

### 4. Create PostgreSQL Database
1. Click **New** → **PostgreSQL**
2. Choose free tier
3. Copy the **Internal Connection String**
4. Add to Backend environment variables

### 5. Run Initial Migration
The Dockerfile runs `alembic upgrade head` automatically on startup.

To run manually:
```bash
alembic upgrade head
```

### 6. Seed Demo Data
```bash
# Set your DATABASE_URL first
python scripts/seed_demo.py
```

## Frontend Deployment (Streamlit Cloud)

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
# Copy production env
cp .env.production .env

# Fill in your values
# DATABASE_URL, OPENAI_API_KEY, SECRET_KEY

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