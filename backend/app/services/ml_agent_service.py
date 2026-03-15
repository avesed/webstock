"""ML Agent Service -- LLM-driven training optimization with checkpoint/resume.

The service implements a tool-calling agent loop that:
1. Profiles market data
2. Generates training configs via LLM reasoning
3. Suspends on training submission (async checkpoint)
4. Resumes when training completes (result injection)
5. Decides: validate -> deploy, retry, or reject
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.llm.types import (
    ChatRequest,
    ChatResponse,
    Message,
    Role,
    ToolCall,
    ToolDefinition,
)
from app.models.prediction import MLBacktest

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).parent.parent / "prompts" / "templates" / "ml_agent_system.md"
)

# Agent loop limits
_MAX_TOOL_ROUNDS = 20  # Max tool-call rounds per session (safety)
_LLM_TIMEOUT = 60  # Per-LLM-call timeout (seconds)


class MLAgentService:
    """ML Engineer Agent with async checkpoint/resume.

    Lifecycle:
        start_session() -> agent_loop -> [suspend on ml_submit_training]
        resume_session(training_result) -> agent_loop -> [validate/deploy/retry]

    Conversation state is serialized to the ``ml_backtests.agent_conversation``
    JSONB column between suspend/resume cycles.
    """

    def __init__(self) -> None:
        self._system_prompt: Optional[str] = None

    def _get_system_prompt(self) -> str:
        if self._system_prompt is None:
            self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")
        return self._system_prompt

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_session(
        self,
        market: str,
        cutoff_date: date,
        validation_days: int,
        forward_days: int,
        max_iterations: int,
        db: AsyncSession,
    ) -> str:
        """Create a backtest record for an agent session.

        Returns the backtest_id.  The actual agent loop should be
        dispatched asynchronously via ``run_agent_session()``.
        """
        backtest_id = str(uuid.uuid4())
        backtest = MLBacktest(
            id=uuid.UUID(backtest_id),
            market=market,
            cutoff_date=cutoff_date,
            validation_days=validation_days,
            forward_days=forward_days,
            effective_config={"max_iterations": max_iterations},
            status="pending",
            agent_run_id=f"ml-agent-{uuid.uuid4().hex[:8]}",
        )
        db.add(backtest)
        await db.commit()
        return backtest_id

    async def run_agent_session(
        self,
        backtest_id: str,
        market: str,
        cutoff_date: date,
        validation_days: int,
        forward_days: int,
        max_iterations: int,
        user_id: int,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Run the agent loop for a backtest session.

        Called from a Celery task.  Updates backtest status to 'running',
        enters the agent loop, and returns when suspended or complete.
        """
        backtest = await db.get(MLBacktest, uuid.UUID(backtest_id))
        if not backtest:
            raise ValueError(f"Backtest {backtest_id} not found")
        backtest.status = "running"
        await db.commit()

        messages = [
            Message(role=Role.SYSTEM, content=self._get_system_prompt()),
            Message(
                role=Role.USER,
                content=self._build_initial_prompt(
                    market,
                    cutoff_date,
                    validation_days,
                    forward_days,
                    max_iterations,
                ),
            ),
        ]

        result = await self._agent_loop(
            backtest_id=backtest_id,
            messages=messages,
            iteration=0,
            max_iterations=max_iterations,
            user_id=user_id,
            db=db,
        )

        return {"backtest_id": backtest_id, **result}

    async def resume_session(
        self,
        backtest_id: str,
        training_result: Dict[str, Any],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Resume a suspended agent session with training results.

        Called by the Celery polling task when training completes.
        Loads the serialized conversation, injects the training result
        as a user message, and re-enters the agent loop.

        The original tool response for ml_submit_training was already
        appended before suspension (it contains the task_id).  The
        training result is injected as a USER message so the LLM sees
        it as new information in the conversation.
        """
        backtest = await db.get(MLBacktest, uuid.UUID(backtest_id))
        if not backtest or not backtest.agent_conversation:
            logger.error(
                "Cannot resume: backtest %s not found or no conversation",
                backtest_id,
            )
            return {"status": "failed", "error": "No conversation state"}

        state = backtest.agent_conversation
        try:
            messages = self._deserialize_messages(state["messages"])
        except (KeyError, ValueError, TypeError) as exc:
            logger.error(
                "Failed to deserialize conversation for backtest %s: %s",
                backtest_id, exc,
            )
            await self._update_backtest_status(
                db, backtest_id, "failed",
                error=f"Conversation deserialization failed: {exc}",
            )
            return {"status": "failed", "error": f"Deserialization failed: {exc}"}

        iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", 3)
        user_id = state.get("user_id", 0)

        # Inject training result as a user message.
        # The tool response (with task_id) was already in the conversation
        # before suspension.  This message carries the actual training
        # metrics back to the agent.
        result_json = json.dumps(training_result, ensure_ascii=False, default=str)
        iteration_note = (
            f"训练完成。当前迭代: {iteration}/{max_iterations}。"
        )
        if iteration >= max_iterations:
            iteration_note += " 这是最后一轮迭代，请做出最终决策。"

        messages.append(
            Message(
                role=Role.USER,
                content=f"{iteration_note}\n\n训练结果:\n```json\n{result_json}\n```",
            )
        )

        # Extract metrics from the result and persist immediately
        resume_metrics: Dict[str, Any] = {}
        tr = training_result.get("result", training_result)
        if isinstance(tr, dict):
            # Training metrics (from ml_submit_training)
            if tr.get("ic") is not None:
                resume_metrics["train_ic"] = tr["ic"]
            if tr.get("icir") is not None:
                resume_metrics["train_icir"] = tr["icir"]
            # Validation metrics (from ml_run_rolling_backtest)
            val_col_map = {
                "val_ic": "val_ic",
                "val_icir": "val_icir",
                "val_spread": "val_spread",
                "val_direction_accuracy": "val_direction_accuracy",
                "val_hit_rate": "val_hit_rate",
                "val_max_drawdown": "val_max_drawdown",
            }
            for src_key, db_col in val_col_map.items():
                v = tr.get(src_key)
                if v is not None:
                    resume_metrics[db_col] = v

        # Mark as running again + write metrics
        values: Dict[str, Any] = {"status": "running", "agent_iteration": iteration}
        values.update(resume_metrics)
        await db.execute(
            update(MLBacktest)
            .where(MLBacktest.id == uuid.UUID(backtest_id))
            .values(**values)
        )
        await db.commit()

        result = await self._agent_loop(
            backtest_id=backtest_id,
            messages=messages,
            iteration=iteration,
            max_iterations=max_iterations,
            user_id=user_id,
            db=db,
        )

        return {"backtest_id": backtest_id, **result}

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    async def _agent_loop(
        self,
        backtest_id: str,
        messages: List[Message],
        iteration: int,
        max_iterations: int,
        user_id: int,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Core tool-calling loop.

        Returns when:
        - Agent produces text without tool_calls (done)
        - Agent calls ml_submit_training (suspend for async training)
        - Max tool rounds exceeded (safety exit)
        - LLM error (failed)
        """
        tools = self._build_tools()
        t0 = time.monotonic()
        tool_history: List[Dict[str, str]] = []
        # Track best metrics seen from tool results for writing on completion
        best_metrics: Dict[str, Any] = {}

        # Set initial progress so the frontend can show "running"
        await self._update_live_progress(
            db, backtest_id, iteration, max_iterations,
            phase="starting", phase_detail="", tool_history=[],
        )

        for round_num in range(_MAX_TOOL_ROUNDS):
            try:
                response = await self._call_llm(messages, tools, db)
            except Exception as exc:
                logger.error(
                    "ML Agent LLM call failed: %s", exc, exc_info=True
                )
                await self._update_backtest_status(
                    db, backtest_id, "failed", error=str(exc)
                )
                return {"status": "failed", "error": str(exc)}

            # ---- Case 1: No tool calls -- agent is done ----
            if not response.tool_calls:
                elapsed = time.monotonic() - t0
                logger.info(
                    "ML Agent completed: backtest=%s, rounds=%d, "
                    "iteration=%d/%d, elapsed=%.1fs, response=%s",
                    backtest_id,
                    round_num + 1,
                    iteration,
                    max_iterations,
                    elapsed,
                    (response.content or "")[:200],
                )
                # Clean up any stale Redis pending key
                await self._delete_pending_task(backtest_id)
                await self._update_backtest_status(
                    db,
                    backtest_id,
                    "completed",
                    agent_conversation=self._build_conversation_state(
                        messages, iteration, max_iterations, user_id,
                        phase="completed",
                        phase_detail=(response.content or "")[:500],
                    ),
                )
                # Write best metrics collected from tool results
                if best_metrics:
                    await self._write_metrics(db, backtest_id, best_metrics)
                return {"status": "completed", "reasoning": response.content}

            # ---- Case 2: Tool calls -- execute each one ----
            messages.append(
                Message(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # Capture LLM reasoning text (if any) before tool calls
            if response.content and response.content.strip():
                tool_history.append({
                    "tool": "_reasoning",
                    "summary": response.content.strip()[:300],
                })

            for tc in response.tool_calls:
                tool_name = tc.name
                try:
                    arguments = json.loads(tc.arguments) if tc.arguments else {}
                except json.JSONDecodeError:
                    logger.warning(
                        "ML Agent: malformed tool args for %s: %s",
                        tc.name, (tc.arguments or "")[:500],
                    )
                    arguments = {}

                summary = self._summarize_tool_call(tool_name, arguments)
                logger.info(
                    "ML Agent tool call: %s(%s) [backtest=%s, round=%d]",
                    tool_name,
                    json.dumps(arguments, ensure_ascii=False)[:200],
                    backtest_id,
                    round_num,
                )

                # Update live progress before executing the tool
                tool_history.append({"tool": tool_name, "summary": summary})
                await self._update_live_progress(
                    db, backtest_id, iteration, max_iterations,
                    phase=tool_name,
                    phase_detail=summary,
                    tool_history=tool_history[-5:],
                )

                # -- SUSPEND path: async training / rolling backtest --
                if tool_name in (
                    "ml_submit_training",
                    "ml_run_rolling_backtest",
                ):
                    # Only increment iteration for training submissions;
                    # rolling backtest is validation — same iteration.
                    next_iter = (
                        iteration + 1
                        if tool_name == "ml_submit_training"
                        else iteration
                    )
                    return await self._handle_training_submission(
                        tc=tc,
                        arguments=arguments,
                        messages=messages,
                        backtest_id=backtest_id,
                        iteration=next_iter,
                        max_iterations=max_iterations,
                        user_id=user_id,
                        db=db,
                        tool_name=tool_name,
                    )

                # -- Normal tool execution --
                tool_result = await self._execute_tool(
                    tool_name, arguments, user_id, db
                )
                messages.append(
                    Message(
                        role=Role.TOOL,
                        content=json.dumps(
                            tool_result, ensure_ascii=False, default=str
                        ),
                        tool_call_id=tc.id,
                        name=tool_name,
                    )
                )

                # Extract metrics from tool results
                self._collect_metrics(tool_name, tool_result, best_metrics)

        # Safety exit
        logger.warning(
            "ML Agent hit max tool rounds (%d) for backtest %s",
            _MAX_TOOL_ROUNDS,
            backtest_id,
        )
        await self._update_backtest_status(
            db, backtest_id, "failed", error="Max tool rounds exceeded"
        )
        return {"status": "failed", "error": "Max tool rounds exceeded"}

    # ------------------------------------------------------------------
    # Training submission (suspend)
    # ------------------------------------------------------------------

    async def _handle_training_submission(
        self,
        tc: ToolCall,
        arguments: Dict[str, Any],
        messages: List[Message],
        backtest_id: str,
        iteration: int,
        max_iterations: int,
        user_id: int,
        db: AsyncSession,
        tool_name: str = "ml_submit_training",
    ) -> Dict[str, Any]:
        """Execute a training/rolling-backtest tool and suspend the agent loop.

        Handles both ml_submit_training and ml_run_rolling_backtest — both
        return a task_id and use the same suspend/resume mechanism.

        The tool result (containing task_id or error) is appended to
        the conversation before serialization so the full exchange is
        captured.

        Returns the suspend status dict or falls through on submission
        failure (lets the agent loop continue to handle the error).
        """
        tool_result = await self._execute_tool(
            tool_name, arguments, user_id, db
        )

        # Always append the tool response so the conversation is complete
        messages.append(
            Message(
                role=Role.TOOL,
                content=json.dumps(
                    tool_result, ensure_ascii=False, default=str
                ),
                tool_call_id=tc.id,
                name=tool_name,
            )
        )

        # Try to extract task_id from the result
        task_id = self._extract_task_id(tool_result)

        if not task_id:
            # Training submission failed -- do NOT suspend.
            # Return a non-suspend status that indicates failure so that
            # the caller (or a future retry) can act on it.
            error_msg = tool_result.get("error", "Unknown training submission error")
            logger.warning(
                "ML Agent training submission failed for backtest %s: %s",
                backtest_id,
                error_msg,
            )
            await self._update_backtest_status(
                db, backtest_id, "failed", error=error_msg
            )
            return {"status": "failed", "error": error_msg}

        # Serialize conversation state and suspend
        config_summary = self._summarize_tool_call("ml_submit_training", arguments)
        conv_state = self._build_conversation_state(
            messages, iteration, max_iterations, user_id,
            phase="training",
            phase_detail=config_summary,
        )
        conv_state["pending_task_id"] = task_id

        await self._update_backtest_status(
            db,
            backtest_id,
            "suspended",
            agent_conversation=conv_state,
            agent_iteration=iteration,
        )

        try:
            await self._set_pending_task(backtest_id, task_id)
        except Exception:
            # Redis failed — cannot set up polling, mark as failed
            await self._update_backtest_status(
                db, backtest_id, "failed",
                error="Failed to register pending task in Redis",
            )
            return {"status": "failed", "error": "Redis unavailable for task tracking"}

        logger.info(
            "ML Agent suspended: backtest=%s, task=%s, iteration=%d/%d",
            backtest_id,
            task_id,
            iteration,
            max_iterations,
        )
        return {
            "status": "suspended",
            "pending_task_id": task_id,
            "iteration": iteration,
        }

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _call_llm(
        self,
        messages: List[Message],
        tools: List[ToolDefinition],
        db: AsyncSession,
    ) -> ChatResponse:
        """Call LLM via gateway with prediction purpose."""
        from app.core.llm.gateway import get_llm_gateway
        from app.services.settings_service import get_settings_service

        gateway = get_llm_gateway()
        settings_svc = get_settings_service()

        resolved = await settings_svc.resolve_model_with_fallback(
            db, ["prediction", "analysis", "chat"]
        )

        pruned = self._prune_messages(messages)

        request = ChatRequest(
            messages=pruned,
            model=resolved.model,
            tools=tools,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=2000,
            timeout=_LLM_TIMEOUT,
        )

        return await gateway.chat(
            request,
            purpose="prediction",
            system_api_key=resolved.api_key,
            system_base_url=resolved.base_url,
        )

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_id: int,
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Execute a tool call and return full, untruncated result.

        Bypasses the chat adapter's 2000-char truncation — the ML agent
        needs complete profile data (baseline_config, feature_nan_rates)
        to make informed tuning decisions.

        Returns {"result": <dict>} on success or {"error": "..."} on failure.
        """
        from app.skills.chat_adapter import _SKILL_TIMEOUTS, TOOL_TIMEOUT_SECONDS
        from app.skills.registry import get_skill_registry

        registry = get_skill_registry()
        skill = registry.get(tool_name)
        if skill is None:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            timeout = _SKILL_TIMEOUTS.get(tool_name, TOOL_TIMEOUT_SECONDS)
            result = await asyncio.wait_for(
                skill.execute(**arguments), timeout=timeout
            )
            if not result.success:
                return {"error": result.error or f"Tool {tool_name} failed"}

            # Return raw data — json.dumps in the agent loop does a
            # single clean serialization into the message content.
            data = result.data
            return {"result": data if isinstance(data, dict) else str(data)}

        except asyncio.TimeoutError:
            logger.warning("ML Agent tool %s timed out", tool_name)
            return {"error": f"Tool {tool_name} timed out"}
        except asyncio.CancelledError:
            logger.warning("ML Agent tool %s cancelled", tool_name)
            return {"error": f"Tool {tool_name} was cancelled"}
        except Exception as e:
            logger.exception("ML Agent tool %s failed: %s", tool_name, e)
            return {"error": f"Tool {tool_name} failed: {e}"}

    @staticmethod
    def _build_tools() -> List[ToolDefinition]:
        """Get ML tool definitions from the skill registry."""
        from app.skills.chat_adapter import ADMIN_SKILL_NAMES, skill_to_tool_definition
        from app.skills.registry import get_skill_registry

        registry = get_skill_registry()
        tools: List[ToolDefinition] = []
        for name in ADMIN_SKILL_NAMES:
            skill = registry.get(name)
            if skill is not None:
                tools.append(skill_to_tool_definition(skill))
        return tools

    # ------------------------------------------------------------------
    # Task ID extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_task_id(tool_result: Dict[str, Any]) -> Optional[str]:
        """Extract task_id from a tool result.

        ``tool_result["result"]`` is a raw dict from PredictionClient
        containing ``{"task_id": "...", "status": "submitted"}``.
        """
        if "error" in tool_result:
            logger.debug("_extract_task_id: tool error: %s", tool_result.get("error"))
            return None

        raw = tool_result.get("result")
        if not raw:
            logger.warning("_extract_task_id: empty result: %s", tool_result)
            return None

        if isinstance(raw, dict):
            tid = raw.get("task_id")
            if not tid:
                logger.warning("_extract_task_id: no task_id in result: %s", raw)
            return tid

        # Fallback: JSON string (shouldn't happen with current _execute_tool)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed.get("task_id") if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                return None

        logger.warning("_extract_task_id: unexpected type: %s", type(raw))
        return None

    # ------------------------------------------------------------------
    # Message helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_initial_prompt(
        market: str,
        cutoff_date: date,
        validation_days: int,
        forward_days: int,
        max_iterations: int,
    ) -> str:
        """Build the initial user message for the agent."""
        return (
            f"请为 {market.upper()} 市场优化 ML 预测模型训练配置。\n\n"
            f"参数:\n"
            f"- 市场: {market}\n"
            f"- 截止日期: {cutoff_date.isoformat()}\n"
            f"- 验证天数: {validation_days}\n"
            f"- 预测天数: {forward_days}\n"
            f"- 最大迭代次数: {max_iterations}\n\n"
            f"请先调用 ml_profile_data 分析数据特征，然后基于分析结果设计训练配置。"
        )

    @staticmethod
    def _prune_messages(
        messages: List[Message], max_messages: int = 40
    ) -> List[Message]:
        """Keep conversation within bounds.

        Always preserves: system prompt (first message) + last N messages.
        Ensures we never split an assistant tool_calls message from its
        corresponding tool response messages.
        """
        if len(messages) <= max_messages:
            return list(messages)

        system = messages[:1]
        tail = messages[1:]  # Everything after system prompt

        # Find a safe cut point: we want to keep at most (max_messages - 1)
        # messages from the tail. Walk backward to find a point that doesn't
        # split an assistant+tool_calls block from its tool responses.
        keep_start = max(0, len(tail) - (max_messages - 1))

        # Walk forward from keep_start to find a message that is NOT a TOOL
        # response (which would be orphaned from its assistant message).
        while keep_start < len(tail) and tail[keep_start].role == Role.TOOL:
            keep_start += 1

        return system + tail[keep_start:]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def _build_conversation_state(
        messages: List[Message],
        iteration: int,
        max_iterations: int,
        user_id: int,
        phase: str = "",
        phase_detail: str = "",
    ) -> Dict[str, Any]:
        """Serialize conversation state for DB storage."""
        # Extract recent tool calls + reasoning for progress display
        tool_history: List[Dict[str, str]] = []
        for m in messages:
            if m.role == Role.ASSISTANT and m.tool_calls:
                # Capture reasoning text before tool calls
                if m.content and m.content.strip():
                    tool_history.append({
                        "tool": "_reasoning",
                        "summary": m.content.strip()[:300],
                    })
                for tc in m.tool_calls:
                    try:
                        args = json.loads(tc.arguments) if tc.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    summary = MLAgentService._summarize_tool_call(tc.name, args)
                    if summary:
                        tool_history.append({"tool": tc.name, "summary": summary})

        return {
            "messages": MLAgentService._serialize_messages(messages),
            "iteration": iteration,
            "max_iterations": max_iterations,
            "user_id": user_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "phase_detail": phase_detail,
            "tool_history": tool_history[-5:],  # Keep last 5 tool calls
        }

    @staticmethod
    def _summarize_tool_call(name: str, args: Dict[str, Any]) -> str:
        """Generate a human-readable summary of a tool call."""
        _TUNABLE_KEYS = [
            "learning_rate", "num_leaves", "num_boost_round",
            "min_child_samples", "lambda_l2", "feature_fraction",
            "bagging_fraction", "early_stopping_rounds",
            "direction_learning_rate", "direction_num_leaves",
            "direction_min_child_samples", "direction_lambda_l2",
        ]

        if name == "ml_profile_data":
            return f"分析 {args.get('market', '?').upper()} 市场数据特征"
        if name == "ml_submit_training":
            tuned = {k: args[k] for k in _TUNABLE_KEYS if args.get(k) is not None}
            if not tuned:
                return "提交训练: 基线配置"
            parts = ", ".join(f"{k}={v}" for k, v in tuned.items())
            return f"提交训练: {parts}"
        if name == "ml_get_training_status":
            return f"查询训练状态: {args.get('task_id', '?')}"
        if name == "ml_run_validation":
            val_days = args.get("validation_days", 60)
            return f"运行静态验证: {val_days}天"
        if name == "ml_run_rolling_backtest":
            interval = args.get("retrain_interval", 5)
            val_days = args.get("validation_days", 60)
            tuned = {k: args[k] for k in _TUNABLE_KEYS if args.get(k) is not None}
            cfg_str = f" ({', '.join(f'{k}={v}' for k, v in tuned.items())})" if tuned else ""
            return f"滚动回测: {val_days}天, 每{interval}天重训练{cfg_str}"
        if name == "ml_deploy_config":
            return "部署模型配置"
        return ""

    @staticmethod
    def _collect_metrics(
        tool_name: str,
        tool_result: Dict[str, Any],
        best_metrics: Dict[str, Any],
    ) -> None:
        """Extract metrics from tool results into best_metrics dict.

        Parses the JSON result string from training status and validation
        tools, keeping the best values seen across iterations.
        """
        if tool_name not in (
            "ml_get_training_status",
            "ml_run_validation",
            "ml_run_rolling_backtest",
        ):
            return

        raw = tool_result.get("result")
        if not raw:
            return
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(parsed, dict):
            return

        # Training status → result sub-dict contains ic, icir
        result_data = parsed.get("result", parsed)
        if not isinstance(result_data, dict):
            result_data = parsed

        if tool_name == "ml_get_training_status":
            ic = result_data.get("ic")
            icir = result_data.get("icir")
            if ic is not None:
                best_metrics["train_ic"] = ic
            if icir is not None:
                best_metrics["train_icir"] = icir
            # Direction model metrics from training
            d_auc = result_data.get("direction_auc")
            d_brier = result_data.get("direction_brier")
            if d_auc is not None:
                best_metrics["direction_auc"] = d_auc
            if d_brier is not None:
                best_metrics["direction_brier"] = d_brier

        elif tool_name in ("ml_run_validation", "ml_run_rolling_backtest"):
            for key in (
                "val_ic", "val_icir", "val_spread",
                "val_direction_accuracy", "val_hit_rate", "val_max_drawdown",
                "val_direction_auc", "val_direction_brier",
                "avg_direction_auc", "avg_direction_brier",
            ):
                v = result_data.get(key)
                if v is not None:
                    best_metrics[key] = v
            # Map rolling BT / validation direction metrics to canonical keys
            if result_data.get("avg_direction_auc") is not None:
                best_metrics["direction_auc"] = result_data["avg_direction_auc"]
            elif result_data.get("val_direction_auc") is not None:
                best_metrics["direction_auc"] = result_data["val_direction_auc"]
            if result_data.get("avg_direction_brier") is not None:
                best_metrics["direction_brier"] = result_data["avg_direction_brier"]
            elif result_data.get("val_direction_brier") is not None:
                best_metrics["direction_brier"] = result_data["val_direction_brier"]

    @staticmethod
    async def _write_metrics(
        db: AsyncSession,
        backtest_id: str,
        metrics: Dict[str, Any],
    ) -> None:
        """Persist collected metrics to the backtest record."""
        # Map metric keys to MLBacktest column names
        col_map = {
            "train_ic": "train_ic",
            "train_icir": "train_icir",
            "val_ic": "val_ic",
            "val_icir": "val_icir",
            "val_spread": "val_spread",
            "val_direction_accuracy": "val_direction_accuracy",
            "val_hit_rate": "val_hit_rate",
            "val_max_drawdown": "val_max_drawdown",
            "direction_auc": "direction_auc",
            "direction_brier": "direction_brier",
        }
        values: Dict[str, Any] = {}
        for metric_key, col_name in col_map.items():
            if metric_key in metrics:
                values[col_name] = metrics[metric_key]

        if not values:
            return

        try:
            await db.execute(
                update(MLBacktest)
                .where(MLBacktest.id == uuid.UUID(backtest_id))
                .values(**values)
            )
            await db.commit()
            logger.info(
                "ML Agent wrote metrics for backtest %s: %s",
                backtest_id,
                {k: f"{v:.4f}" if isinstance(v, float) else v for k, v in values.items()},
            )
        except Exception as exc:
            logger.warning(
                "Failed to write metrics for backtest %s: %s",
                backtest_id,
                exc,
            )
            try:
                await db.rollback()
            except Exception:
                pass

    @staticmethod
    def _serialize_messages(messages: List[Message]) -> List[Dict[str, Any]]:
        """Convert Message objects to JSON-serializable dicts."""
        result: List[Dict[str, Any]] = []
        for m in messages:
            d: Dict[str, Any] = {"role": m.role.value, "content": m.content}
            if m.tool_calls:
                d["tool_calls"] = [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            result.append(d)
        return result

    @staticmethod
    def _deserialize_messages(data: List[Dict[str, Any]]) -> List[Message]:
        """Reconstruct Message objects from serialized dicts."""
        messages: List[Message] = []
        for d in data:
            tool_calls = None
            if d.get("tool_calls"):
                tool_calls = [
                    ToolCall(
                        id=tc["id"], name=tc["name"], arguments=tc["arguments"]
                    )
                    for tc in d["tool_calls"]
                ]
            messages.append(
                Message(
                    role=Role(d["role"]),
                    content=d.get("content"),
                    tool_calls=tool_calls,
                    tool_call_id=d.get("tool_call_id"),
                    name=d.get("name"),
                )
            )
        return messages

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    @staticmethod
    async def _update_backtest_status(
        db: AsyncSession,
        backtest_id: str,
        status: str,
        error: Optional[str] = None,
        agent_conversation: Optional[Dict[str, Any]] = None,
        agent_iteration: Optional[int] = None,
    ) -> None:
        """Update backtest record status and optional fields."""
        from sqlalchemy import func

        values: Dict[str, Any] = {"status": status}
        if error is not None:
            values["error"] = error[:2000]
        if agent_conversation is not None:
            values["agent_conversation"] = agent_conversation
        if agent_iteration is not None:
            values["agent_iteration"] = agent_iteration
        if status in ("completed", "failed"):
            values["completed_at"] = datetime.now(timezone.utc)
            # Compute duration from created_at
            values["duration_seconds"] = func.extract(
                "epoch", func.now() - MLBacktest.created_at
            )

        await db.execute(
            update(MLBacktest)
            .where(MLBacktest.id == uuid.UUID(backtest_id))
            .values(**values)
        )
        await db.commit()

    @staticmethod
    async def _set_pending_task(backtest_id: str, task_id: str) -> None:
        """Store pending task mapping in Redis for Celery polling.

        The key ``ml_agent:pending:{backtest_id}`` maps to the
        data-processor task_id so the polling task can look up
        which backtest to resume when training completes.

        Raises on failure so the caller can mark the session as failed
        rather than silently orphaning it.
        """
        try:
            from app.db.redis import get_redis

            redis = await get_redis()
            await redis.set(
                f"ml_agent:pending:{backtest_id}", task_id, ex=7200
            )
        except Exception as exc:
            logger.error(
                "CRITICAL: Failed to set Redis pending task for "
                "backtest %s (task %s): %s -- session will be orphaned",
                backtest_id,
                task_id,
                exc,
            )
            raise

    @staticmethod
    async def _update_live_progress(
        db: AsyncSession,
        backtest_id: str,
        iteration: int,
        max_iterations: int,
        phase: str,
        phase_detail: str,
        tool_history: List[Dict[str, str]],
    ) -> None:
        """Write lightweight progress to effective_config for frontend polling.

        This is called frequently during the agent loop so the status endpoint
        can serve real-time progress without deserializing the full conversation.
        Uses raw SQL merge into the existing JSONB to preserve max_iterations
        and any other fields already stored there.
        """
        from sqlalchemy import text

        try:
            await db.execute(
                text("""
                    UPDATE ml_backtests
                    SET effective_config = COALESCE(effective_config, '{}'::jsonb)
                        || jsonb_build_object(
                            'iteration', :iteration,
                            'max_iterations', :max_iterations,
                            'phase', :phase,
                            'phase_detail', :phase_detail,
                            'tool_history', :tool_history::jsonb
                        )
                    WHERE id = :backtest_id
                """),
                {
                    "backtest_id": backtest_id,
                    "iteration": iteration,
                    "max_iterations": max_iterations,
                    "phase": phase,
                    "phase_detail": phase_detail,
                    "tool_history": json.dumps(tool_history, ensure_ascii=False),
                },
            )
            await db.commit()
        except Exception as exc:
            logger.warning(
                "Failed to update live progress for backtest %s: %s",
                backtest_id,
                exc,
            )
            # Non-critical — don't fail the agent loop for a progress update
            try:
                await db.rollback()
            except Exception:
                pass

    @staticmethod
    async def _delete_pending_task(backtest_id: str) -> None:
        """Remove pending task key from Redis (cleanup on completion)."""
        try:
            from app.db.redis import get_redis

            redis = await get_redis()
            await redis.delete(f"ml_agent:pending:{backtest_id}")
        except Exception:
            pass  # Best-effort cleanup; key has TTL anyway


# Module-level singleton
ml_agent_service = MLAgentService()
