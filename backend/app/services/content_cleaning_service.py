"""Layer 1.5 content cleaning and visual data extraction service.

Uses a small vision-capable LLM to:
1. Conservatively clean text — ONLY remove content that is 100% certain to be
   non-article junk (ads, navigation, cookie banners, social buttons, "related
   articles" sidebars). When in doubt, KEEP the content.
2. Extract information from embedded images (financial charts, tables, rankings).
   The LLM acts as a pure information extractor — it does not make editorial
   decisions or judge article quality.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm import get_llm_gateway, ChatRequest, Message, Role

logger = logging.getLogger(__name__)

# Maximum input text length (chars) to send to the LLM
MAX_TEXT_LENGTH = 12000

# Maximum number of images to include in a single request
MAX_IMAGES = 3


CLEANING_SYSTEM_PROMPT = """\
你是新闻内容预处理器，负责两项任务：保守清洗文本 + 提取图片信息。

## 任务1: 保守清洗文本

仅删除100%确定不属于文章正文的内容：
- 网站导航栏、页头页脚、面包屑导航
- 广告、推广、赞助商内容
- Cookie提示、隐私政策弹窗文本
- 社交分享按钮文字（"分享到微信/微博"、"Tweet this"）
- "相关阅读"、"推荐文章"、"你可能喜欢"列表
- 评论区内容
- 版权声明模板（非文章内容的网站通用声明）

**重要原则**：
- 有任何疑问时，保留原文
- 不要修改、改写、总结或重新组织任何正文内容
- 不要删除作者信息、发布时间、数据来源说明
- 不要删除文章中引用的任何数据、报价或事实
- 输出的cleaned_text应该和原文高度相似，只是少了明显的垃圾内容

## 任务2: 提取图片信息

如果有图片，用自然语言描述图片中的关键数据：
- 图表中的具体数字、趋势、时间范围
- 表格中的关键行列数据
- K线图的价格区间、成交量
- 排行榜的具体排名和数值

如果没有图片或图片不包含有用数据，image_insights设为空字符串。

## 输出JSON格式
{
  "cleaned_text": "清洗后的文本（应接近原文长度）",
  "image_insights": "从图片中提取的数据描述",
  "has_critical_visual_data": false
}"""


def _extract_json_from_response(text: str) -> Dict[str, Any]:
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse cleaning JSON: %s, text: %s", e, text[:200])
        return {}


@dataclass
class CleaningResult:
    """Result of content cleaning."""

    cleaned_text: str
    image_insights: str
    has_visual_data: bool


class ContentCleaningService:
    """Conservative content cleaner and visual data extractor.

    Designed as a preprocessing step that ALL articles pass through:
    - Text is cleaned conservatively (only 100% certain junk removed)
    - Images are analyzed for extractable data (charts, tables, etc.)
    - The LLM never makes editorial decisions about content quality
    """

    async def _resolve_model_config(self, db: AsyncSession):
        """Resolve LLM model configuration for content cleaning.

        Uses purpose "phase2_layer15_cleaning", falls back to "news_filter".
        """
        from app.services.settings_service import get_settings_service

        settings_service = get_settings_service()
        try:
            resolved = await settings_service.resolve_model_provider(
                db, "phase2_layer15_cleaning"
            )
        except ValueError:
            logger.debug(
                "No phase2_layer15_cleaning provider configured, "
                "falling back to news_filter"
            )
            resolved = await settings_service.resolve_model_provider(db, "news_filter")

        if not resolved.api_key:
            raise ValueError(
                "No API key configured for content cleaning. "
                "Please configure a phase2_layer15_cleaning or news_filter "
                "model assignment in Admin Settings."
            )

        return resolved

    def _build_content_parts(
        self,
        full_text: str,
        images: Optional[List[Dict[str, str]]],
    ) -> List[Dict[str, Any]]:
        """Build multimodal content parts for the LLM request.

        Args:
            full_text: Raw article text
            images: List of {"url", "base64", "mime"} dicts, or None

        Returns:
            List of content part dicts for Message.content
        """
        truncated_text = full_text[:MAX_TEXT_LENGTH]
        if len(full_text) > MAX_TEXT_LENGTH:
            truncated_text += "\n\n[... 文本已截断 ...]"

        parts: List[Dict[str, Any]] = [
            {"type": "text", "text": truncated_text},
        ]

        # Add base64-encoded image parts
        if images:
            for img in images[:MAX_IMAGES]:
                b64 = img.get("base64")
                mime = img.get("mime", "image/jpeg")
                if not b64:
                    continue
                data_uri = f"data:{mime};base64,{b64}"
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                })

        return parts

    async def clean_and_extract(
        self,
        db: AsyncSession,
        full_text: str,
        images: Optional[List[Dict[str, str]]],
        url: str,
    ) -> CleaningResult:
        """Clean article text and extract image insights via LLM.

        This runs for ALL articles regardless of whether images are present.
        Text cleaning is conservative (only remove obvious junk).

        Args:
            db: Database session for resolving model config
            full_text: Raw article text
            images: List of {"url", "base64", "mime"} dicts, or None
            url: Article URL for logging context

        Returns:
            CleaningResult with cleaned text and image insights.
            On any failure, returns original text (fail-open).
        """
        t0 = time.monotonic()
        url_short = url[:80] if url else "<unknown>"

        if not full_text or not full_text.strip():
            logger.warning(
                "[ContentCleaning] Empty text for %s, returning empty result",
                url_short,
            )
            return CleaningResult(
                cleaned_text="",
                image_insights="",
                has_visual_data=False,
            )

        try:
            model_config = await self._resolve_model_config(db)
        except ValueError as e:
            logger.error(
                "[ContentCleaning] Cannot resolve model config: %s. "
                "Returning original text for %s",
                e, url_short,
            )
            return CleaningResult(
                cleaned_text=full_text,
                image_insights="",
                has_visual_data=False,
            )

        image_count = len(images) if images else 0

        # Build multimodal content parts
        content_parts = self._build_content_parts(full_text, images)

        messages = [
            Message(role=Role.SYSTEM, content=CLEANING_SYSTEM_PROMPT),
            Message(role=Role.USER, content=content_parts),
        ]

        model_name = model_config.model or "gpt-4o-mini"

        logger.info(
            "[ContentCleaning] Starting LLM call for %s, model=%s, "
            "text_len=%d, images=%d",
            url_short, model_name, len(full_text), image_count,
        )

        try:
            gateway = get_llm_gateway()
            chat_request = ChatRequest(
                model=model_name,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=90,
            )

            llm_start = time.monotonic()
            response = await gateway.chat(
                chat_request,
                system_api_key=model_config.api_key,
                system_base_url=model_config.base_url,
                use_user_config=False,
                purpose="content_cleaning",
                usage_metadata={"url": url_short},
            )
            llm_elapsed_ms = (time.monotonic() - llm_start) * 1000

            content = response.content or ""
            result = _extract_json_from_response(content)

            if not result:
                logger.warning(
                    "[ContentCleaning] JSON parse failed for %s (%.0fms), "
                    "response length=%d. Returning original text.",
                    url_short, llm_elapsed_ms, len(content),
                )
                return CleaningResult(
                    cleaned_text=full_text,
                    image_insights="",
                    has_visual_data=False,
                )

            cleaned_text = result.get("cleaned_text", "")
            image_insights = result.get("image_insights", "")
            has_visual_data = bool(result.get("has_critical_visual_data", False))

            # Safety: if cleaned text lost > 50% of original, LLM over-cleaned.
            # Use original text instead.
            if cleaned_text and len(cleaned_text) < len(full_text) * 0.5:
                logger.warning(
                    "[ContentCleaning] Cleaned text lost >50%% "
                    "(%d chars vs %d original) for %s. Using original.",
                    len(cleaned_text), len(full_text), url_short,
                )
                cleaned_text = full_text

            if not cleaned_text.strip():
                cleaned_text = full_text

            total_elapsed_ms = (time.monotonic() - t0) * 1000

            usage_str = ""
            if response.usage:
                usage_str = (
                    f", tokens(in={response.usage.prompt_tokens}, "
                    f"out={response.usage.completion_tokens})"
                )

            logger.info(
                "[ContentCleaning] Completed for %s: model=%s, "
                "cleaned_len=%d, insights_len=%d, has_visual=%s, "
                "llm=%.0fms, total=%.0fms%s",
                url_short, model_name,
                len(cleaned_text), len(image_insights), has_visual_data,
                llm_elapsed_ms, total_elapsed_ms, usage_str,
            )

            return CleaningResult(
                cleaned_text=cleaned_text,
                image_insights=image_insights,
                has_visual_data=has_visual_data,
            )

        except Exception as e:
            total_elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error(
                "[ContentCleaning] LLM call failed for %s (%.0fms): %s. "
                "Returning original text.",
                url_short, total_elapsed_ms, e,
            )
            return CleaningResult(
                cleaned_text=full_text,
                image_insights="",
                has_visual_data=False,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_service: Optional[ContentCleaningService] = None


def get_content_cleaning_service() -> ContentCleaningService:
    """Get singleton instance of ContentCleaningService."""
    global _service
    if _service is None:
        _service = ContentCleaningService()
    return _service


def reset_content_cleaning_service() -> None:
    """Reset singleton (used by Celery worker lifecycle)."""
    global _service
    _service = None
