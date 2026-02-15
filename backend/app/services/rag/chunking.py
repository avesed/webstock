"""Text chunking implementations for the RAG subsystem."""

from __future__ import annotations
from typing import List


class LangChainRecursiveChunker:
    """CJK-aware chunking via LangChain RecursiveCharacterTextSplitter.

    Adds Chinese sentence-end characters as separators for better
    boundary detection in mixed CJK/English text.
    """

    def __init__(
        self,
        separators: List[str] | None = None,
        keep_separator: bool = True,
    ):
        self._separators = separators or [
            "\n\n", "\n", "\u3002", "\uff01", "\uff1f", ".", "!", "?", " ", ""
        ]
        self._keep_separator = keep_separator

    def chunk(
        self,
        text: str,
        *,
        max_chars: int = 1500,
        overlap_chars: int = 150,
    ) -> List[str]:
        if not text:
            return []
        if len(text) <= max_chars:
            return [text]

        # Lazy import to avoid startup cost in Celery workers
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chars,
            chunk_overlap=overlap_chars,
            separators=self._separators,
            keep_separator=self._keep_separator,
            length_function=len,
        )
        return splitter.split_text(text)


class ParagraphSentenceChunker:
    """Original paragraph -> sentence -> character chunker.

    Preserved as fallback. Migrated from EmbeddingService.chunk_text().
    """

    def chunk(
        self,
        text: str,
        *,
        max_chars: int = 1500,
        overlap_chars: int = 150,
    ) -> List[str]:
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
