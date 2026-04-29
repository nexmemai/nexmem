"""OpenAI embedding service for generating vector embeddings."""

import logging
from typing import List, Optional

import openai
import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


class Embedder:
    """Service for generating text embeddings using OpenAI API."""

    def __init__(self):
        self.client = openai.OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_embedding_model
        self.vector_dim = settings.vector_dim

    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: The text to embed

        Returns:
            List of floats representing the embedding vector
        """
        try:
            # Truncate text to avoid token limit issues
            truncated = text[:8000]

            response = self.client.embeddings.create(
                model=self.model,
                input=truncated
            )

            return response.data[0].embedding

        except openai.APIError as e:
            logger.error(f"OpenAI API error during embedding: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during embedding: {e}")
            raise

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed

        Returns:
            List of embedding vectors
        """
        try:
            # Truncate texts
            truncated = [t[:8000] for t in texts]

            response = self.client.embeddings.create(
                model=self.model,
                input=truncated
            )

            return [item.embedding for item in response.data]

        except openai.APIError as e:
            logger.error(f"OpenAI API error during batch embedding: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during batch embedding: {e}")
            raise

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        v1 = np.array(vec1)
        v2 = np.array(vec2)
        return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

    def random_vector(self) -> List[float]:
        """Generate a random normalized vector for demo/fallback purposes."""
        vec = np.random.randn(self.vector_dim)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()


# Global embedder instance
embedder = Embedder()
EmbeddingService = Embedder
