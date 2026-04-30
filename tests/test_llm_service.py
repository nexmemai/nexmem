"""
Task 6.1: Unit tests for the LLM service with mocked OpenAI responses.
Tests tenacity retry logic and fallback behaviour without real API calls.
"""
import pytest
from unittest.mock import MagicMock, patch
import openai


class TestLLMServiceRetries:
    """Tests for the tenacity retry behaviour in the LLM service."""

    @patch("app.services.llm.openai.OpenAI")
    def test_generate_rag_response_success(self, mock_openai_cls):
        """A successful API call should return the reply and token counts."""
        # Arrange
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="Hello from LLM"))],
            usage=MagicMock(prompt_tokens=50, completion_tokens=20),
        )

        from app.services.llm import LLMService
        svc = LLMService()

        # Act
        result = svc.generate_rag_response(user_message="Hi!")

        # Assert
        assert result["reply"] == "Hello from LLM"
        assert result["prompt_tokens"] == 50
        assert result["completion_tokens"] == 20

    @patch("app.services.llm.openai.OpenAI")
    def test_retries_on_rate_limit_then_succeeds(self, mock_openai_cls):
        """Should retry on RateLimitError and eventually succeed."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client

        # First two calls raise, third succeeds
        mock_client.chat.completions.create.side_effect = [
            openai.RateLimitError("rate limit", response=MagicMock(), body={}),
            openai.RateLimitError("rate limit", response=MagicMock(), body={}),
            MagicMock(
                choices=[MagicMock(message=MagicMock(content="Retry success"))],
                usage=MagicMock(prompt_tokens=30, completion_tokens=10),
            ),
        ]

        from app.services.llm import LLMService
        svc = LLMService()

        # tenacity's wait_exponential will fire but with min=2s — patch it to avoid slow tests
        with patch("tenacity.wait_exponential", return_value=lambda _: 0):
            result = svc.generate_rag_response(user_message="Retry me")

        assert result["reply"] == "Retry success"
        assert mock_client.chat.completions.create.call_count == 3

    @patch("app.services.llm.openai.OpenAI")
    def test_fails_after_max_retries(self, mock_openai_cls):
        """Should raise after exhausting all retries."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai.APIConnectionError(
            request=MagicMock()
        )

        from app.services.llm import LLMService
        svc = LLMService()

        with patch("tenacity.wait_exponential", return_value=lambda _: 0):
            with pytest.raises(openai.APIConnectionError):
                svc.generate_rag_response(user_message="This will fail")


class TestLLMServiceInterface:
    """Tests for the LLM service response structure."""

    @patch("app.services.llm.openai.OpenAI")
    def test_response_contains_latency(self, mock_openai_cls):
        """The response dict must always include latency_ms."""
        mock_client = MagicMock()
        mock_openai_cls.return_value = mock_client
        mock_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="OK"))],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        )

        from app.services.llm import LLMService
        svc = LLMService()
        result = svc.generate_rag_response(user_message="latency check")

        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], float)
