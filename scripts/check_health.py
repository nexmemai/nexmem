#!/usr/bin/env python
"""
scripts/check_health.py — Standalone health check script.

Usage:
    python scripts/check_health.py
    python scripts/check_health.py --url https://api.example.com

Checks:
    1. /health/live — liveness probe
    2. /health/ready — readiness probe (includes DB + embedding check)
"""

import argparse
import asyncio
import sys
import httpx
from typing import Optional


async def check_liveness(url: str) -> tuple[bool, dict]:
    """Check /health/live endpoint."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{url}/health/live")
            if response.status_code == 200:
                return True, response.json()
            return False, {"error": f"Status {response.status_code}"}
    except Exception as e:
        return False, {"error": str(e)}


async def check_readiness(url: str) -> tuple[bool, dict]:
    """Check /health/ready endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{url}/health/ready")
            data = response.json()
            is_ready = response.status_code == 200 and data.get("status") == "ready"
            return is_ready, data
    except Exception as e:
        return False, {"error": str(e)}


async def check_api_call(url: str, api_key: Optional[str]) -> tuple[bool, str]:
    """Test an authenticated API call if key provided."""
    if not api_key:
        return True, "skipped (no API key)"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            headers = {"Authorization": f"ApiKey {api_key}"}
            response = await client.get(f"{url}/api/v1/auth/me", headers=headers)
            if response.status_code == 200:
                return True, "authenticated call works"
            return False, f"status {response.status_code}"
    except Exception as e:
        return False, str(e)


async def main(url: str, api_key: Optional[str] = None):
    print(f"\nChecking: {url}\n")

    liveness_ok, liveness_data = await check_liveness(url)
    print(f"[{'✓' if liveness_ok else '✗'}] Liveness: {liveness_data}")

    readiness_ok, readiness_data = await check_readiness(url)
    print(f"[{'✓' if readiness_ok else '✗'}] Readiness: {readiness_data}")

    if "checks" in readiness_data:
        for check_name, check_result in readiness_data["checks"].items():
            status = "✓" if check_result == "ok" or "skipped" in check_result else "✗"
            print(f"  {status} {check_name}: {check_result}")

    auth_ok, auth_msg = await check_api_call(url, api_key)
    print(f"[{'✓' if auth_ok else '✗'}] Auth: {auth_msg}")

    print()
    if liveness_ok and readiness_ok:
        print("✓ All checks passed!")
        sys.exit(0)
    else:
        print("✗ Some checks failed.")
        sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Health check for AI Memory Layer API")
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--key", default=None, help="API key for auth test")
    args = parser.parse_args()

    asyncio.run(main(args.url, args.key))