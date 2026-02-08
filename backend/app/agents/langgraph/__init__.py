"""LangGraph-based AI agents for stock analysis.

This module provides the LangGraph implementation of the layered LLM architecture
for stock analysis. It includes:

- Analysis agents: Fundamental, Technical, Sentiment, News
- Synthesis layer: Combines individual agent results
- Workflow: Orchestrates parallel agent execution and clarification rounds
- Utilities: JSON extraction, conflict detection, state management
"""

from app.agents.langgraph.state import (
    AnalysisState,
    create_initial_state,
    get_completed_agents,
    get_successful_results,
)
from app.agents.langgraph.utils import (
    detect_conflicts,
    detect_low_confidence_results,
    extract_json_from_response,
    extract_structured_data,
    get_consensus_action,
    safe_json_extract,
)
from app.agents.langgraph.workflow import (
    create_analysis_workflow,
    get_workflow,
    get_workflow_info,
    run_analysis,
    run_single_agent,
    stream_analysis,
)

__all__ = [
    # State management
    "AnalysisState",
    "create_initial_state",
    "get_completed_agents",
    "get_successful_results",
    # Utilities
    "detect_conflicts",
    "detect_low_confidence_results",
    "extract_json_from_response",
    "extract_structured_data",
    "get_consensus_action",
    "safe_json_extract",
    # Workflow
    "create_analysis_workflow",
    "get_workflow",
    "get_workflow_info",
    "run_analysis",
    "run_single_agent",
    "stream_analysis",
]
