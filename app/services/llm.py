"""LLM service for RAG-enhanced responses."""

import logging
import time
from typing import List, Optional

import openai

from app.config import settings

logger = logging.getLogger(__name__)

TOKEN_COST_PER_1K = {
    "gpt-4o": {"prompt": 0.005, "completion": 0.015},
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
}


def track_token_usage(
    user_id: str,
    app_id: Optional[str],
    prompt_tokens: int,
    completion_tokens: int,
    model: str,
) -> None:
    """Log token usage to database for billing."""
    from app.database import async_session
    from app.models.user import TokenUsage
    import uuid

    total = prompt_tokens + completion_tokens
    cost = TOKEN_COST_PER_1K.get(model, {"prompt": 0, "completion": 0})
    cost_cents = int(
        (prompt_tokens / 1000 * cost["prompt"] + completion_tokens / 1000 * cost["completion"]) * 100
    )

    async def _insert():
        async with async_session() as session:
            # Apply RLS context so the INSERT passes the policy added in
            # 013_extend_rls (token_usage_user_isolation).
            from app.database import set_rls_context

            await set_rls_context(session, str(user_id))
            usage = TokenUsage(
                id=uuid.uuid4(),
                user_id=uuid.UUID(user_id),
                app_id=app_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
                model=model,
                cost_cents=cost_cents,
            )
            session.add(usage)
            await session.commit()
            logger.info(f"Tracked {total} tokens for user {user_id}: ${cost_cents/100:.4f}")

    from asgiref.sync import async_to_sync
    async_to_sync(_insert)()


class LLMService:
    """Service for generating LLM responses with memory-augmented context."""

    def __init__(self):
        self.client = openai.OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_llm_model

    def generate_rag_response(
        self,
        user_message: str,
        episodic_context: Optional[List[str]] = None,
        semantic_context: Optional[List[str]] = None,
        procedural_context: Optional[dict] = None,
        graph_context: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        app_id: Optional[str] = None,
    ) -> dict:
        """
        Generate an LLM response augmented with memory context.

        Args:
            user_message: The user's input message
            episodic_context: List of recent conversation memories
            semantic_context: List of semantically relevant memories
            procedural_context: User's procedural memory (settings/workflows)
            graph_context: Relevant knowledge graph nodes/edges
            user_id: User ID for tracking token usage
            app_id: App ID for tracking token usage

        Returns:
            Dict with reply, usage stats, and latency
        """
        from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
        
        start_time = time.time()

        system_prompt = self._build_system_prompt(
            episodic_context=episodic_context or [],
            semantic_context=semantic_context or [],
            procedural_context=procedural_context,
            graph_context=graph_context or [],
        )

        @retry(
            wait=wait_exponential(multiplier=1, min=2, max=10),
            stop=stop_after_attempt(3),
            retry=retry_if_exception_type((openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)),
            reraise=True
        )
        def _call_llm():
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                temperature=0.7,
                max_tokens=1024,
            )

        try:
            response = _call_llm()
            latency_ms = (time.time() - start_time) * 1000

            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens

            if user_id and not settings.demo_mode:
                track_token_usage(
                    user_id=user_id,
                    app_id=app_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    model=self.model,
                )

            return {
                "reply": response.choices[0].message.content,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": round(latency_ms, 2),
            }

        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during LLM generation: {e}")
            raise

    def _build_system_prompt(
        self,
        episodic_context: List[str],
        semantic_context: List[str],
        procedural_context: Optional[dict],
        graph_context: List[str],
    ) -> str:
        """Build the system prompt with memory context."""
        sections = [
            "You are a helpful AI assistant with access to persistent memory about the user.",
            "Use the memory context below to provide personalized, context-aware responses.",
            "If the memory contains relevant information, reference it naturally.",
            "If the memory doesn't contain relevant information, just answer normally.",
            "",
        ]

        if episodic_context:
            sections.append("## Recent Conversation History")
            sections.append("These are recent interactions from your conversation history:")
            for i, ep in enumerate(episodic_context[:5], 1):
                sections.append(f"  {i}. {ep[:200]}")
            sections.append("")

        if semantic_context:
            sections.append("## Relevant Memories")
            sections.append("These memories are semantically related to the current context:")
            for i, sem in enumerate(semantic_context[:5], 1):
                sections.append(f"  {i}. {sem[:200]}")
            sections.append("")

        if procedural_context:
            sections.append("## User Preferences & Settings")
            settings_data = procedural_context.get("settings", {})
            workflows = procedural_context.get("workflows", [])
            if settings_data:
                sections.append(f"  Preferences: {settings_data}")
            if workflows:
                wf_names = [w.get("name", "unnamed") for w in workflows[:3]]
                sections.append(f"  Active workflows: {', '.join(wf_names)}")
            sections.append("")

        if graph_context:
            sections.append("## Knowledge Graph")
            sections.append("Related concepts and connections:")
            for node in graph_context[:5]:
                sections.append(f"  - {node}")
            sections.append("")

        sections.append("## Response Guidelines")
        sections.append("- Be helpful, accurate, and concise")
        sections.append("- Reference memory context when relevant")
        sections.append("- Respect user preferences (tone, style, etc.)")
        sections.append("- If you're unsure, say so rather than making things up")

        return "\n".join(sections)


# Global LLM service instance
llm_service = LLMService()
