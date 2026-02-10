"""Base types for the Skills system.

A Skill is a reusable, async capability unit that wraps data-fetching,
computation, or processing logic.  Skills are consumed by:
  - LangGraph analysis agents (programmatic pre-configured invocation)
  - Chat function-calling (via chat_adapter.py -> ToolDefinition conversion)
  - News processing pipeline (LangGraph workflow nodes)
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SkillParameter:
    """Single parameter definition for a skill."""

    name: str
    type: str  # JSON Schema type: "string", "number", "integer", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: Optional[List[str]] = None
    default: Any = None


@dataclass
class SkillDefinition:
    """Metadata describing a skill (for documentation and ToolDefinition conversion)."""

    name: str
    description: str
    category: str  # "market_data", "news", "user_data", "knowledge", "computation"
    parameters: List[SkillParameter] = field(default_factory=list)

    def to_json_schema(self) -> Dict[str, Any]:
        """Convert to JSON Schema format (compatible with ToolDefinition.parameters)."""
        properties: Dict[str, Any] = {}
        required: List[str] = []
        for p in self.parameters:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not None:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema


@dataclass
class SkillResult:
    """Result of a skill execution."""

    success: bool
    data: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseSkill(ABC):
    """Abstract base class for all skills."""

    @abstractmethod
    def definition(self) -> SkillDefinition:
        """Return this skill's metadata."""
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> SkillResult:
        """Execute the skill with the given parameters."""
        ...

    @property
    def name(self) -> str:
        return self.definition().name

    @property
    def category(self) -> str:
        return self.definition().category

    async def safe_execute(self, timeout: float = 15.0, **kwargs: Any) -> SkillResult:
        """Execute with timeout and error handling.  Never raises."""
        start = time.time()
        try:
            result = await asyncio.wait_for(self.execute(**kwargs), timeout=timeout)
            result.metadata.setdefault("latency_ms", int((time.time() - start) * 1000))
            return result
        except asyncio.TimeoutError:
            latency = int((time.time() - start) * 1000)
            logger.warning("Skill %s timed out after %.1fs", self.name, timeout)
            return SkillResult(
                success=False,
                error=f"Skill {self.name} timed out after {timeout}s",
                metadata={"latency_ms": latency},
            )
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            logger.exception("Skill %s execution failed: %s", self.name, e)
            return SkillResult(
                success=False,
                error=f"Skill {self.name} failed: {str(e)}",
                metadata={"latency_ms": latency},
            )
