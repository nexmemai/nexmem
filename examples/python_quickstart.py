#!/usr/bin/env python
"""
Nexmem Python SDK — local end-to-end quickstart.

This script bootstraps a brand-new account against a *local* Nexmem
backend, mints an API key, then drives the published ``nexmem-py``
SDK end-to-end (remember + recall). Everything runs against
``http://localhost:8000`` by default, which is the port a fresh
``uvicorn app.main:app --reload`` listens on.

Prereqs (one terminal):

    # from the repo root, with Python 3.10+:
    pip install -e nexmem-py             # install the SDK from source
    pip install -r requirements.txt      # install backend deps
    pip install httpx                    # used here for auth bootstrap

    # start the backend in DEMO_MODE so no Postgres / Redis is needed:
    DEMO_MODE=true uvicorn app.main:app --reload --port 8000

In a second terminal:

    python examples/python_quickstart.py

Flags:

    --url    base URL of the backend          (default: http://localhost:8000)
    --email  email to register / log in as    (default: a unique demo address)

This script never hard-codes a real API key or password. The password
used for the throwaway demo account is intentionally weak and is only
suitable for ``DEMO_MODE=true`` local runs. Do not point this script at
a production deployment.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from typing import Any

import httpx

from nexmem import MemoryClient


DEFAULT_BASE_URL = "http://localhost:8000"


def banner(step: int, title: str) -> None:
    bar = "=" * 60
    print(f"\n{bar}\nSTEP {step}: {title}\n{bar}")


async def bootstrap_api_key(base_url: str, email: str, password: str) -> str:
    """Register a fresh user, log in, and mint a single ``nxm_`` API key.

    Returns the raw key so it can be handed to the SDK. The backend
    only ever returns the raw key once; the script does not persist
    it to disk.
    """
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as http:
        banner(1, "Register user")
        resp = await http.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        if resp.status_code not in (200, 201):
            print(f"register failed: status {resp.status_code}")
            resp.raise_for_status()
        print(f"registered {email}")

        banner(2, "Log in (get short-lived JWT)")
        resp = await http.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        resp.raise_for_status()
        access_token = resp.json()["access_token"]
        # Never log the token or its length — CodeQL flags any sensitive
        # value reaching a logging sink as clear-text logging.
        print("access_token received [REDACTED — do not log in production]")

        banner(3, "Create API key (returned once, prefix nxm_)")
        resp = await http.post(
            "/api/v1/auth/api-keys",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"name": "local-quickstart"},
        )
        resp.raise_for_status()
        raw_key: str = resp.json()["api_key"]
        if not raw_key.startswith("nxm_"):
            # Report only a static message; never echo any part of the key.
            raise RuntimeError(
                "unexpected API key prefix from backend (expected nxm_)"
            )
        print("api_key created (prefix nxm_) [REDACTED — do not log in production]")
        return raw_key


async def run(base_url: str, email: str) -> None:
    # Throwaway password for DEMO_MODE only. Generated fresh per run so
    # nothing identifying ever lands in the script source.
    password = "demo-pw-" + secrets.token_hex(8)

    api_key = await bootstrap_api_key(base_url, email, password)

    banner(4, "Open SDK client pointed at local backend")
    async with MemoryClient(api_key=api_key, base_url=base_url) as client:
        banner(5, "remember(): write an episodic memory")
        episode = await client.remember(
            "User prefers Python for backend work and concise answers.",
            metadata={"source": "python_quickstart"},
        )
        print(f"episodic_id  = {episode.episodic_id}")
        print(f"semantic_id  = {episode.semantic_id}")
        print(f"engram_id    = {episode.engram_id}")

        banner(6, "remember(): write a second episode")
        await client.remember(
            "User is building a memory layer with FastAPI and pgvector.",
            metadata={"source": "python_quickstart"},
        )
        print("second episode written")

        banner(7, "recall(): retrieve assembled context for a query")
        context = await client.recall(
            "what is the user working on and how do they like to be answered?",
            limit=5,
        )
        # ``context.content`` and ``context.memories.content`` are the
        # same assembled string. We print a trimmed preview to keep
        # the demo output readable.
        preview = context.content[:400]
        suffix = "..." if len(context.content) > 400 else ""
        print(f"\nAssembled context ({len(context.content)} chars):")
        print("-" * 40)
        print(preview + suffix)
        print("-" * 40)
        print(f"semantic hits: {len(context.memories.semantic_hits)}")
        print(f"recent episodes: {len(context.memories.recent_episodes)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Nexmem Python SDK local end-to-end quickstart"
    )
    p.add_argument("--url", default=DEFAULT_BASE_URL, help="Backend base URL")
    # Default email is unique-per-run so re-running does not collide
    # with a previously registered demo user.
    default_email = f"demo-{secrets.token_hex(4)}@example.local"
    p.add_argument("--email", default=default_email, help="Demo account email")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(run(args.url, args.email))
    except httpx.ConnectError as exc:
        print(
            "\nCould not reach the backend at "
            f"{args.url}. Is `uvicorn app.main:app --reload --port 8000` running?\n"
            f"({exc})",
            file=sys.stderr,
        )
        return 2
    print("\nDone. Local quickstart succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
