# 🚀 NexMem Deployment Guide (Render + Supabase)

This document contains the definitive configuration requirements for successfully deploying the NexMem API to **Render** using **Supabase** as the database provider.

---

## 🛠 1. Database Setup (Supabase)

NexMem requires the **Supabase Connection Pooler** for compatibility with Render's network (which does not support IPv6-only direct hostnames).

### **Pooler Configuration**
1. Go to your **Supabase Dashboard** -> **Settings** -> **Database**.
2. Scroll down to the **Connection Pooler** section.
3. **Mode**: Set to `Transaction` (recommended).
4. **Hostname**: Use the pooler hostname (e.g., `aws-0-ap-south-1.pooler.supabase.com`).
5. **Port**: `6543` (This is critical. Do **not** use 5432).
6. **User**: `postgres.[your-project-ref]`

### **Password Encoding**
If your database password contains special characters, they **must** be URL-encoded in your connection string:
- `@` → `%40`
- `#` → `%23`
- `:` → `%3A`

---

## 🌐 2. Render Configuration

### **Environment Variables**
Configure these in the **Render Dashboard** under **Environment**:

| Key | Value | Description |
| :--- | :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:6543/postgres` | Your pooler connection string. |
| `DEMO_MODE` | `false` | **Required** to use the database instead of in-memory storage. |
| `PYTHON_VERSION` | `3.11.9` | Matches the project's required Python version. |
| `PORT` | `8000` | Render will typically set this automatically. |

### **Commands**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## 🛡 3. Critical Technical Safeguards

The following features are implemented in the code to ensure stability on Render:

1.  **Fail-Safe Override**: If the app detects a direct Supabase hostname (which often fails on Render), it will attempt to redirect to the verified Mumbai pooler.
2.  **Alembic sys.path Hack**: `alembic/env.py` includes a path injection to ensure it can find the `app` module at runtime.
3.  **Driver Synchronization**:
    *   The **App** uses `asyncpg` for high-performance async queries.
    *   **Alembic** uses `psycopg2` for synchronous schema migrations.
    *   The system automatically handles the conversion between these drivers.

---

## 🔍 4. Troubleshooting

### **"Network is unreachable"**
- **Cause**: You are likely using the direct Supabase hostname instead of the Pooler.
- **Fix**: Switch the host to the `pooler.supabase.com` address and port `6543`.

### **"ModuleNotFoundError: No module named 'app'"**
- **Cause**: The Python path is not correctly set for the Alembic process.
- **Fix**: This is handled in the current `alembic/env.py` via `sys.path.insert`.

### **"Invalid interpolation"**
- **Cause**: A `%` character in your password is being misinterpreted by the config parser.
- **Fix**: The code automatically escapes `%` to `%%` for Alembic.

---

## ✅ 5. Health Check
Once deployed, verify your service is alive at:
`https://your-service-name.onrender.com/health/live`
