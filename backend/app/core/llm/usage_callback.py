"""LangChain callback handler for LLM usage recording.

LangGraph nodes use LangChain's BaseChatModel (ChatOpenAI/ChatAnthropic)
which bypasses the gateway. This callback handler intercepts on_llm_end
events to record token usage via the module-level _usage_recorder.
"""

import logging
from typing import Any, Dict, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)


class LlmUsageCallbackHandler(AsyncCallbackHandler):
    """Async callback handler that records LLM usage for cost tracking.

    Attach to LangChain model calls:
        handler = LlmUsageCallbackHandler(purpose="analysis", metadata={...})
        result = await llm.ainvoke(messages, config={"callbacks": [handler]})
    """

    def __init__(
        self,
        purpose: str,
        user_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__()
        self.purpose = purpose
        self.user_id = user_id
        self.metadata = metadata

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Extract usage from LLM response and record it."""
        from app.core.llm.gateway import _usage_recorder
        if not _usage_recorder:
            return

        try:
            # Extract usage from LLMResult.llm_output
            llm_output = response.llm_output or {}
            token_usage = llm_output.get("token_usage", {})

            prompt_tokens = token_usage.get("prompt_tokens", 0) or 0
            completion_tokens = token_usage.get("completion_tokens", 0) or 0
            cached_tokens = token_usage.get("cached_tokens", 0) or 0

            # Model name from llm_output or generations
            model = llm_output.get("model_name", "")
            if not model and response.generations:
                gen = response.generations[0]
                if gen and hasattr(gen[0], "generation_info"):
                    info = gen[0].generation_info or {}
                    model = info.get("model", "")

            if not (prompt_tokens or completion_tokens):
                return

            await _usage_recorder(
                purpose=self.purpose,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                user_id=self.user_id,
                metadata=self.metadata,
            )
        except Exception:
            logger.debug("LangChain usage recording failed", exc_info=True)
