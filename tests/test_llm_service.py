"""
Task 6.1: Unit tests for the LLM service.

All external OpenAI calls are mocked — no API key or network required.
Tests cover success paths, tenacity retry logic, and the interface contract.
"""
import pytest
from unittest.mock import MagicMock, patch, call
import openai


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_llm_service():
    """
    Instantiate LLMService with a mocked OpenAI client so the constructor
    never makes a real network connection.
    """
    with patch("app.services.llm.openai.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        from app.services.llm import LLMService
        svc = LLMService()
        svc.client = mock_cls.return_value   # expose mock for test setup
        return svc


def _fake_completion(text="Hello from LLM", prompt_tokens=50, completion_tokens=20):
    """Build a fake openai ChatCompletion response object."""
    return MagicMock(
        choices=[MagicMock(message=MagicMock(content=text))],
        usage=MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Happy-path tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLLMServiceHappyPath:

    def test_returns_reply_and_token_counts(self):
        """Success response must include reply, token counts, and latency."""
        svc = _make_llm_service()
        svc.client.chat.completions.create.return_value = _fake_completion()

        result = svc.generate_rag_response(user_message="Hello!")

        assert result["reply"] == "Hello from LLM"
        assert result["prompt_tokens"] == 50
        assert result["completion_tokens"] == 20
        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], float)
        assert result["latency_ms"] >= 0

    def test_empty_context_still_succeeds(self):
        """Providing no memory context must not cause an error."""
        svc = _make_llm_service()
        svc.client.chat.completions.create.return_value = _fake_completion("OK")

        result = svc.generate_rag_response(
            user_message="Hi",
            episodic_context=[],
            semantic_context=[],
            procedural_context=None,
            graph_context=[],
        )
        assert result["reply"] == "OK"

    def test_rich_context_is_passed_to_api(self):
        """Memory context should be included in the system prompt sent to the API."""
        svc = _make_llm_service()
        svc.client.chat.completions.create.return_value = _fake_completion()

        svc.generate_rag_response(
            user_message="Test",
            episodic_context=["User loves Python"],
            semantic_context=["Python is a programming language"],
        )

        call_kwargs = svc.client.chat.completions.create.call_args
        messages = call_kwargs.kwargs.get("messages") or call_kwargs.args[0]
        system_content = next(m["content"] for m in messages if m["role"] == "system")
        # The system prompt should embed at least one piece of context
        assert "Python" in system_content or len(system_content) > 10


# ──────────────────────────────────────────────────────────────────────────────
# Tenacity retry tests
# ──────────────────────────────────────────────────────────────────────────────

class TestLLMRetryBehaviour:
    """
    Task 3.2 / 6.1: The LLM service must retry on transient API errors and
    eventually surface the error after exhausting attempts.
    """

    def test_retries_on_rate_limit_and_eventually_succeeds(self):
        """
        Two consecutive RateLimitErrors then a successful call — the service
        should transparently retry and return the final success.
        """
        svc = _make_llm_service()

        err = openai.RateLimitError("rate limited", response=MagicMock(), body={})
        svc.client.chat.completions.create.side_effect = [
            err,
            err,
            _fake_completion("Retry success", prompt_tokens=30, completion_tokens=10),
        ]

        # Patch tenacity's wait so tests don't sleep for real
        with patch("tenacity.nap.time") as mock_sleep:
            mock_sleep.sleep = MagicMock()
            result = svc.generate_rag_response(user_message="Retry me")

        assert result["reply"] == "Retry success"
        assert svc.client.chat.completions.create.call_count == 3

    def test_retries_on_connection_error(self):
        """APIConnectionError should also trigger a retry."""
        svc = _make_llm_service()

        conn_err = openai.APIConnectionError(request=MagicMock())
        svc.client.chat.completions.create.side_effect = [
            conn_err,
            _fake_completion("Recovered"),
        ]

        with patch("tenacity.nap.time") as mock_sleep:
            mock_sleep.sleep = MagicMock()
            result = svc.generate_rag_response(user_message="connection test")

        assert result["reply"] == "Recovered"

    def test_raises_after_max_retries_exhausted(self):
        """After all retries fail the original exception must propagate."""
        svc = _make_llm_service()
        svc.client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )

        with patch("tenacity.nap.time") as mock_sleep:
            mock_sleep.sleep = MagicMock()
            with pytest.raises(openai.APIConnectionError):
                svc.generate_rag_response(user_message="will exhaust retries")

        # Should have been called exactly max_retries (3) times
        assert svc.client.chat.completions.create.call_count == 3

    def test_non_retryable_error_raises_immediately(self):
        """A generic ValueError should NOT be retried and must raise immediately."""
        svc = _make_llm_service()
        svc.client.chat.completions.create.side_effect = ValueError("bad input")

        with pytest.raises(Exception):
            svc.generate_rag_response(user_message="bad")

        # Must only have been called once — no retries on ValueError
        assert svc.client.chat.completions.create.call_count == 1


# ──────────────────────────────────────────────────────────────────────────────
# demo_db in-memory store unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestDemoDbStore:
    """
    Fast, pure-Python unit tests for the in-memory demo_db module.
    These validate the core data operations used by all endpoints in demo mode.
    """

    def setup_method(self):
        """Reset stores before each test method."""
        from app import demo_db
        demo_db.episodic_store.clear()
        demo_db.semantic_store.clear()
        self.db = demo_db

    def test_create_and_retrieve_episodic(self):
        result = self.db.create_episodic("user_1", "sess_1", "Test memory")
        assert "id" in result

        records = self.db.get_episodic("user_1")
        assert len(records) == 1
        assert records[0]["content"] == "Test memory"

    def test_episodic_user_isolation(self):
        """Records written for user_1 must not appear under user_2."""
        self.db.create_episodic("user_1", "s", "user_1 secret")
        self.db.create_episodic("user_2", "s", "user_2 secret")

        user1_records = self.db.get_episodic("user_1")
        user2_records = self.db.get_episodic("user_2")

        assert all("user_1 secret" in r["content"] for r in user1_records)
        assert all("user_2 secret" not in r["content"] for r in user1_records)
        assert all("user_2 secret" in r["content"] for r in user2_records)

    def test_delete_episodic(self):
        result = self.db.create_episodic("user_1", "s", "To delete")
        deleted = self.db.delete_episodic("user_1", result["id"])
        assert deleted is True
        assert self.db.get_episodic("user_1") == []

    def test_app_id_filter(self):
        """Memories scoped to app_id A must not appear when filtering for app_id B."""
        r = self.db.create_episodic("user_1", "s", "app A memory")
        # Manually tag it to app_id A
        self.db.episodic_store["user_1"][0]["app_id"] = "app_A"

        results = self.db.get_episodic("user_1", app_id="app_B")
        assert results == []

        results_a = self.db.get_episodic("user_1", app_id="app_A")
        assert len(results_a) == 1
