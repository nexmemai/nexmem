"""Load test for the NexMem backend.

Usage:
    locust -f tests/locustfile.py --host http://localhost:8000

History:
    The original version of this file POSTed to `/api/v1/episodic/` and
    `/api/v1/rag/chat` with payloads that did not match the routers — every
    "load" request returned 422 / 404. The "load test" was load-testing
    error paths.

This rewrite:
    - Registers a unique user per virtual user (Locust spawns many).
    - Captures the real `user_id` from `/api/v1/auth/me`, since both
      `/agents/{user_id}/episodes` and `/rag/chat` validate that the
      path/body user_id matches the authenticated user (403 otherwise).
    - Uses route shapes that exist:
        POST /api/v1/agents/{user_id}/episodes
        POST /api/v1/rag/chat
        GET  /health/live
    - Uses the real Pydantic body shapes (session_id + content for the
      episodic write; user_id + message for RAG chat).

A single-worker server can sustain this load up to the per-user quota
(default 1000/month for free tier). For sustained load testing you should
pre-create users on a higher tier, or set FREE_MONTHLY_WRITES high and
flush Redis between runs.
"""

from __future__ import annotations

import random
import uuid

from locust import HttpUser, between, task


SAMPLE_MESSAGES = [
    "What is my preferred coding language?",
    "Summarise what I know about machine learning.",
    "What did I say about my project deadlines?",
    "Do I have any notes on database optimisation?",
    "What are my long-term goals?",
]

SAMPLE_MEMORIES = [
    "The user prefers Python for backend development.",
    "Meeting with Alice scheduled for next Monday.",
    "Need to review the Q3 roadmap by end of week.",
    "User is learning Rust for systems programming.",
    "Preferred database: PostgreSQL with pgvector.",
]


class NexmemAPIUser(HttpUser):
    """Simulates an AI agent that registers, then loops on write + chat."""

    wait_time = between(0.5, 2)
    token: str = ""
    user_id: str = ""

    def on_start(self) -> None:
        """Register + login + capture user_id once per virtual user."""
        unique = uuid.uuid4().hex[:10]
        email = f"loadtest_{unique}@example.com"
        password = "LoadTest!2026demo"

        # Register; ignore 400 (duplicate) so retried virtual users still work.
        self.client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
            name="POST /auth/register",
        )

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
            name="POST /auth/login",
        )
        if login.status_code != 200:
            return

        self.token = login.json().get("access_token", "")
        if not self.token:
            return

        me = self.client.get(
            "/api/v1/auth/me",
            headers=self._headers,
            name="GET /auth/me",
        )
        if me.status_code == 200:
            self.user_id = me.json()["id"]

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    @task(3)
    def write_episode(self) -> None:
        """High-frequency: write an episodic memory (real route)."""
        if not self.user_id:
            return
        self.client.post(
            f"/api/v1/agents/{self.user_id}/episodes",
            json={
                "session_id": f"load_session_{random.randint(1, 10)}",
                "content": random.choice(SAMPLE_MEMORIES),
                "metadata": {},
                "tags": ["load-test"],
            },
            headers=self._headers,
            name="POST /agents/{user_id}/episodes",
        )

    @task(2)
    def query_rag_chat(self) -> None:
        """Medium-frequency: query memory context via RAG."""
        if not self.user_id:
            return
        self.client.post(
            "/api/v1/rag/chat",
            json={
                "user_id": self.user_id,
                "message": random.choice(SAMPLE_MESSAGES),
                "include_episodic": True,
                "include_semantic": True,
                "include_procedural": False,
                "include_graph": False,
                "top_k": 5,
            },
            headers=self._headers,
            name="POST /rag/chat",
        )

    @task(1)
    def health_check(self) -> None:
        """Low-frequency: liveness probe (simulates uptime monitoring)."""
        self.client.get("/health/live", name="GET /health/live")
