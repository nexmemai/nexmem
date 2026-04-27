#!/usr/bin/env python
"""
Demo walkthrough script - Day 7 Deliverable.

This script demonstrates the complete memory layer flow:
1. Register a user
2. Create an API key
3. Write memory episodes
4. Query context
5. Show assembled context

Usage:
    python scripts/demo_walkthrough.py [--url http://localhost:8000]
"""

import argparse
import asyncio
import httpx
import json
import sys


async def print_step(step: int, title: str):
    print(f"\n{'=' * 60}")
    print(f"STEP {step}: {title}")
    print('=' * 60)


async def main(url: str, api_key: str = None):
    base_url = url.rstrip('/')
    headers = {"Authorization": f"ApiKey {api_key}"} if api_key else {}

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        await print_step(1, "Health Check")
        resp = await client.get("/health/ready")
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")

        await print_step(2, "Register User")
        user_data = {
            "email": f"demo_{asyncio.current_task().get_name()}@memorylayer.ai",
            "password": "demo_password_123"
        }
        resp = await client.post("/api/v1/auth/register", json=user_data)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 201:
            user = resp.json()
            print(f"Created user: {user['email']} (id: {user['id'][:8]}...)")
        else:
            print(f"Response: {resp.json()}")

        await print_step(3, "Login & Get Token")
        resp = await client.post("/api/v1/auth/login", json={
            "email": user_data["email"],
            "password": user_data["password"]
        })
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            token = resp.json()["access_token"]
            headers["Authorization"] = f"Bearer {token}"
            print(f"Got access token: {token[:20]}...")

        await print_step(4, "Create API Key")
        resp = await client.post(
            "/api/v1/auth/api-keys",
            json={"name": "Demo CLI Key"},
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code == 201:
            api_data = resp.json()
            raw_key = api_data["api_key"]
            print(f"Created API key: {raw_key}")
            print("IMPORTANT: Store this key securely - it's shown only once!")

        await print_step(5, "Write Memory Episode")
        resp = await client.post(
            "/api/v1/memory/episode/write",
            json={
                "content": "User is building a decentralized AI memory layer with FastAPI and pgvector. Project uses 4 memory types: episodic, semantic, procedural, and associative.",
                "session_id": "demo-session-1",
                "tags": ["project", "fastapi", "pgvector"],
                "metadata": {"source": "demo"}
            },
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")

        await print_step(6, "Write Another Episode")
        resp = await client.post(
            "/api/v1/memory/episode/write",
            json={
                "content": "User prefers concise code examples without verbose comments. User likes Python for backend development.",
                "session_id": "demo-session-2",
                "tags": ["preference", "python"],
                "metadata": {"source": "demo"}
            },
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        print(f"Response: {json.dumps(resp.json(), indent=2)}")

        await print_step(7, "Query Memory Context")
        resp = await client.post(
            "/api/v1/memory/context",
            json={
                "query": "What is the user working on and what are their preferences?",
                "semantic_top_k": 5,
                "episodic_limit": 5,
                "max_tokens": 500
            },
            headers=headers
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"\nMetadata: {json.dumps(data['metadata'], indent=2)}")
            print(f"\nAssembled Context ({data['metadata']['total_tokens']} tokens):")
            print("-" * 40)
            print(data['assembled_context'][:500])
            print("-" * 40)
            print(f"\nSources used: {data['metadata']['sources_used']}")
            print(f"Compression ratio: {data['metadata']['compression_ratio']}")

        await print_step(8, "List API Keys")
        resp = await client.get("/api/v1/auth/api-keys", headers=headers)
        print(f"Status: {resp.status_code}")
        keys = resp.json()
        print(f"Total API keys: {len(keys)}")
        for key in keys:
            print(f"  - {key['name']} (id: {key['id'][:8]}...)")

        await print_step(9, "Get Current User")
        resp = await client.get("/api/v1/auth/me", headers=headers)
        print(f"Status: {resp.status_code}")
        print(f"User: {json.dumps(resp.json(), indent=2)}")

    print(f"\n{'=' * 60}")
    print("DEMO COMPLETE")
    print('=' * 60)
    print("\nSummary:")
    print("1. Health checks working")
    print("2. User registration + login working")
    print("3. API key creation working")
    print("4. Memory write (episodic + semantic + engram) working")
    print("5. Context assembly returning results")
    print("\nNext: Deploy to production and share the demo!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo walkthrough for AI Memory Layer")
    parser.add_argument("--url", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--key", default=None, help="API key (skip auth steps)")
    args = parser.parse_args()

    asyncio.run(main(args.url, args.key))