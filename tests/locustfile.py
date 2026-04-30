"""
Task 6.2: Load testing script using Locust.
Run with: locust -f tests/locustfile.py --host http://localhost:8000

Tests the two critical high-traffic paths:
  1. Memory write  (POST /api/v1/episodic/)
  2. RAG retrieval (POST /api/v1/rag/chat)
"""
import json
import random
from locust import HttpUser, task, between

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
    """
    Simulates an AI agent continuously writing memories and querying context.
    Each virtual user authenticates once, then hammers the write and read paths.
    """
    wait_time = between(0.5, 2)
    token: str = ""
    user_id: str = ""

    def on_start(self):
        """Authenticate once per virtual user at startup."""
        email = f"loadtest_{random.randint(1, 999999)}@nexmem.test"
        password = "LoadTest!2024"

        self.client.post("/api/v1/auth/register", json={
            "email": email, "password": password
        })
        r = self.client.post("/api/v1/auth/login", json={
            "email": email, "password": password
        })
        if r.status_code == 200:
            self.token = r.json().get("access_token", "")
            self.user_id = f"load_user_{random.randint(1, 9999)}"

    @property
    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    @task(3)
    def write_memory(self):
        """High-frequency task: write an episodic memory."""
        self.client.post(
            "/api/v1/episodic/",
            json={
                "content": random.choice(SAMPLE_MEMORIES),
                "user_id": self.user_id,
                "session_id": f"load_session_{random.randint(1, 10)}",
            },
            headers=self._headers,
            name="/api/v1/episodic/ [write]",
        )

    @task(2)
    def query_context(self):
        """Medium-frequency task: query memory context via RAG."""
        self.client.post(
            "/api/v1/rag/chat",
            json={
                "message": random.choice(SAMPLE_MESSAGES),
                "user_id": self.user_id,
            },
            headers=self._headers,
            name="/api/v1/rag/chat [query]",
        )

    @task(1)
    def health_check(self):
        """Low-frequency task: health probe (simulates uptime monitoring)."""
        self.client.get("/health/live", name="/health/live")
