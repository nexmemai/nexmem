import asyncio
import asyncpg
import os

# asyncpg expects postgresql:// or postgres://
DB_URL = "postgresql://postgres:***REDACTED_PASSWORD***@db.***REDACTED_PROJECT_ID***.supabase.co:5432/postgres"

async def run_sql_file(conn, file_path):
    print(f"Running {file_path}...")
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return
        
    with open(file_path, 'r', encoding='utf-8') as f:
        sql = f.read()
    
    try:
        # Use execute() which can handle multiple statements in a single string for PostgreSQL
        await conn.execute(sql)
        print(f"Successfully applied {file_path}")
    except Exception as e:
        print(f"Error applying {file_path}: {e}")

async def main():
    print("Connecting to Supabase...")
    conn = await asyncpg.connect(DB_URL)
    try:
        # Run 001
        await run_sql_file(conn, "supabase/migrations/001_initial_schema.sql")
        # Run 002
        await run_sql_file(conn, "supabase/migrations/002_day2_auth_and_engrams.sql")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
