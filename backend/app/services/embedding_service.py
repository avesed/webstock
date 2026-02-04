"""Embedding service for generating and managing vector embeddings."""

import logging
from typing import List, Optional

from app.config import settings
from app.core.openai_client import get_openai_client
from app.core.token_bucket import get_embedding_rate_limiter

logger = logging.getLogger(__name__)

# Maximum tokens per embedding request (text-embedding-3-small limit is 8191)
MAX_CHUNK_TOKENS = 512
# Overlap between chunks for context continuity
CHUNK_OVERLAP_TOKENS = 50


class EmbeddingService:
    """
    Service for generating text embeddings using OpenAI's API.

    Handles:
    - Text chunking for long documents
    - Rate-limited embedding generation
    - Batch processing
    """

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed (will be truncated if too long)

        Returns:
            Embedding vector or None on failure
        """
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None

        # Rate limit check
        rate_limiter = await get_embedding_rate_limiter()
        if not await rate_limiter.acquire():
            logger.warning("Embedding rate limit exceeded")
            return None

        try:
            client = get_openai_client()
            response = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=text[:8000],  # Truncate to stay within token limits
                dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
            )
            embedding = response.data[0].embedding
            logger.debug(
                "Generated embedding (dim=%d) for text of length %d",
                len(embedding),
                len(text),
            )
            return embedding
        except Exception as e:
            logger.error("Failed to generate embedding: %s", e)
            return None

    async def generate_embeddings_batch(
        self,
        texts: List[str],
    ) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed

        Returns:
            List of embeddings (None for failed items)
        """
        if not texts:
            return []

        # Filter empty texts
        valid_texts = []
        valid_indices = []
        for i, text in enumerate(texts):
            if text and text.strip():
                valid_texts.append(text[:8000])
                valid_indices.append(i)

        if not valid_texts:
            return [None] * len(texts)

        # Rate limit check
        rate_limiter = await get_embedding_rate_limiter()
        if not await rate_limiter.acquire():
            logger.warning("Embedding batch rate limit exceeded")
            return [None] * len(texts)

        try:
            client = get_openai_client()
            response = await client.embeddings.create(
                model=settings.OPENAI_EMBEDDING_MODEL,
                input=valid_texts,
                dimensions=settings.OPENAI_EMBEDDING_DIMENSIONS,
            )

            # Map results back to original indices
            results: List[Optional[List[float]]] = [None] * len(texts)
            for item in response.data:
                original_idx = valid_indices[item.index]
                results[original_idx] = item.embedding

            logger.info("Generated %d embeddings in batch", len(response.data))
            return results
        except Exception as e:
            logger.error("Failed to generate batch embeddings: %s", e)
            return [None] * len(texts)

    def chunk_text(
        self,
        text: str,
        max_chars: int = 1500,
        overlap_chars: int = 150,
    ) -> List[str]:
        """
        Split text into overlapping chunks for embedding.

        Uses paragraph boundaries when possible, falls back to
        sentence boundaries, then character boundaries.

        Each chunk is guaranteed to be <= max_chars.  Overlap is created by
        carrying forward the tail of the previous chunk as a *prefix* for the
        next chunk, and the budget for new content is reduced accordingly so
        the total never exceeds max_chars.

        Args:
            text: Text to chunk
            max_chars: Maximum characters per chunk
            overlap_chars: Overlap between chunks (must be < max_chars)

        Returns:
            List of text chunks
        """
        if not text or len(text) <= max_chars:
            return [text] if text else []

        # Clamp overlap to a sane fraction of max_chars
        overlap_chars = min(overlap_chars, max_chars // 3)

        # --- Step 1: split into raw (non-overlapping) segments ------------
        raw_segments: List[str] = []
        paragraphs = text.split("\n\n")
        current_segment = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_segment) + len(para) + 2 <= max_chars:
                current_segment += ("\n\n" + para) if current_segment else para
            else:
                if current_segment:
                    raw_segments.append(current_segment)
                # If a single paragraph is too long, split by sentences
                if len(para) > max_chars:
                    sentences = para.replace(". ", ".\n").split("\n")
                    current_segment = ""
                    for sentence in sentences:
                        if len(current_segment) + len(sentence) + 1 <= max_chars:
                            current_segment += (
                                (" " + sentence) if current_segment else sentence
                            )
                        else:
                            if current_segment:
                                raw_segments.append(current_segment)
                            # If a single sentence exceeds max_chars, hard-cut it
                            if len(sentence) > max_chars:
                                for start in range(0, len(sentence), max_chars):
                                    raw_segments.append(sentence[start : start + max_chars])
                                current_segment = ""
                            else:
                                current_segment = sentence
                else:
                    current_segment = para

        if current_segment:
            raw_segments.append(current_segment)

        if not raw_segments:
            return []

        # --- Step 2: build overlapping chunks (each <= max_chars) ---------
        chunks: List[str] = [raw_segments[0]]
        for i in range(1, len(raw_segments)):
            prev_tail = chunks[-1][-overlap_chars:]
            candidate = prev_tail + " " + raw_segments[i]
            if len(candidate) <= max_chars:
                chunks.append(candidate)
            else:
                # Trim the overlap prefix so that total stays within budget
                available = max_chars - len(raw_segments[i]) - 1  # -1 for space
                if available > 0:
                    trimmed_tail = chunks[-1][-available:]
                    chunks.append(trimmed_tail + " " + raw_segments[i])
                else:
                    # Segment itself fills the budget; no room for overlap
                    chunks.append(raw_segments[i][:max_chars])

        return chunks


# Singleton
_embedding_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """Get singleton EmbeddingService instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
