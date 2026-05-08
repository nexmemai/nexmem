import asyncio
import asyncpg
import os


def get_database_url() -> str:
    database_url = os.environ["DATABASE_URL"].strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL must be set before running this script.")
    # asyncpg expects postgresql:// or postgres://, not SQLAlchemy driver URLs.
    return (
        database_url
        .replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgresql+psycopg2://", "postgresql://", 1)
    )

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
    conn = await asyncpg.connect(get_database_url())
    try:
        # Run 001
        await run_sql_file(conn, "supabase/migrations/001_initial_schema.sql")
        # Run 002
        await run_sql_file(conn, "supabase/migrations/002_day2_auth_and_engrams.sql")
    finally:
        await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
