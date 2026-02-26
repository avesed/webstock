"""RD-Agent subprocess management.

Manages the RD-Agent automated factor research process as an isolated
subprocess. Uses Redis distributed lock for single-instance enforcement.

Architecture:
- subprocess.Popen launches RD-Agent with environment overrides
- OPENAI_API_BASE points to local LLM proxy endpoint
- OPENAI_API_KEY set to dummy (proxy handles real auth)
- Progress tracked via stdout log parsing
- Discovered factors parsed from output and registered
"""

import asyncio
import logging
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import redis

from app.config import get_settings
from app.core.settings_cache import settings_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Redis lock key pattern and TTL (24 hours)
_LOCK_KEY_PATTERN = "rdagent:lock:{market}"
_LOCK_TTL = 86400

# Maximum process runtime before forced kill (24 hours)
_MAX_RUNTIME_SECONDS = 86400

# Graceful shutdown timeout before SIGKILL
_SIGTERM_WAIT_SECONDS = 30

# Maximum log lines retained in memory per task
_MAX_LOG_LINES = 100

# Stdout parsing patterns
_RE_ROUND = re.compile(r"Round\s+(\d+)[/\s](\d+)", re.IGNORECASE)
_RE_FACTOR_DISCOVERED = re.compile(
    r"Factor\s+discovered:\s*(.+?)(?:\s*IC[=:]\s*([-\d.]+))?(?:\s*ICIR[=:]\s*([-\d.]+))?$",
    re.IGNORECASE,
)
_RE_IC = re.compile(r"IC[=:]\s*([-\d.]+)", re.IGNORECASE)
_RE_EXPRESSION = re.compile(r"expression[=:]\s*(.+?)(?:\s*,|\s*$)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Task dataclass
# ---------------------------------------------------------------------------


@dataclass
class RDAgentTask:
    """In-memory representation of an RD-Agent research task."""

    market: str
    status: str = "idle"  # idle, starting, running, completed, failed, stopped
    current_round: int = 0
    max_rounds: int = 30
    discovered_count: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    log_lines: list[str] = field(default_factory=list)  # last N lines

    def to_dict(self) -> dict:
        """Serialize to API-compatible dict."""
        return {
            "market": self.market,
            "status": self.status,
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "discovered_count": self.discovered_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "log_tail": self.log_lines[-20:],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_redis_client: Optional[redis.Redis] = None


def _get_redis_client() -> redis.Redis:
    """Return a module-level shared Redis client (lazy singleton)."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _acquire_lock(market: str) -> bool:
    """Try to acquire the distributed lock for a market. Returns True on success."""
    r = _get_redis_client()
    key = _LOCK_KEY_PATTERN.format(market=market)
    acquired = r.set(key, "1", nx=True, ex=_LOCK_TTL)
    return bool(acquired)


def _release_lock(market: str) -> None:
    """Release the distributed lock for a market."""
    r = _get_redis_client()
    key = _LOCK_KEY_PATTERN.format(market=market)
    r.delete(key)


# ---------------------------------------------------------------------------
# RDAgentRunner
# ---------------------------------------------------------------------------


class RDAgentRunner:
    """Manages RD-Agent subprocess lifecycle.

    Supports one concurrent process per market. Uses Redis SET NX for
    distributed locking so multiple data-processor instances cannot
    start duplicate runs.
    """

    def __init__(self) -> None:
        self._processes: dict[str, subprocess.Popen] = {}
        self._tasks: dict[str, RDAgentTask] = {}
        self._monitors: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        market: str,
        universe_id: str | None = None,
        max_rounds: int = 30,
    ) -> dict:
        """Start an RD-Agent research loop for the given market.

        Args:
            market: Market code (us, hk, cn).
            universe_id: Optional universe UUID to scope symbols.
            max_rounds: Maximum number of research iterations.

        Returns:
            Task status dict on success, or dict with "error" key on failure.
        """
        async with self._lock:
            # Check if already running
            if market in self._processes and self._processes[market].poll() is None:
                return {"error": f"RD-Agent already running for market {market}"}

            # Try distributed lock
            if not _acquire_lock(market):
                return {"error": f"RD-Agent locked by another instance for market {market}"}

            # Resolve universe symbols
            symbols = await self._resolve_symbols(market, universe_id)
            if not symbols:
                _release_lock(market)
                return {"error": f"No symbols found for market {market}"}

            # Create task
            task = RDAgentTask(
                market=market,
                status="starting",
                max_rounds=max_rounds,
                started_at=datetime.now(),
            )
            self._tasks[market] = task

            # Launch subprocess in thread (Popen can briefly block)
            try:
                process = await asyncio.to_thread(
                    self._launch_subprocess, market, symbols, max_rounds,
                )
                self._processes[market] = process
                task.status = "running"
                logger.info(
                    "RD-Agent started: market=%s, pid=%d, symbols=%d, max_rounds=%d",
                    market, process.pid, len(symbols), max_rounds,
                )
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                _release_lock(market)
                logger.error("Failed to launch RD-Agent for %s: %s", market, e)
                return {"error": f"Launch failed: {e}"}

            # Start background monitoring
            monitor = asyncio.create_task(
                self._monitor_process(market),
                name=f"rdagent-monitor-{market}",
            )
            self._monitors[market] = monitor

            return task.to_dict()

    def _launch_subprocess(
        self,
        market: str,
        symbols: list[str],
        max_rounds: int,
    ) -> subprocess.Popen:
        """Launch the RD-Agent subprocess with environment overrides.

        This runs in a thread via asyncio.to_thread since Popen may
        briefly block on fork/exec.

        Args:
            market: Market code.
            symbols: List of stock symbols for the research universe.
            max_rounds: Maximum research rounds.

        Returns:
            Popen process handle.
        """
        settings = get_settings()

        env = {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/app"),
            "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
            "OPENAI_API_BASE": f"http://127.0.0.1:{settings.PORT}/v1/llm",
            "OPENAI_API_KEY": "dummy",
            "RDAGENT_MARKET": market,
            "RDAGENT_MAX_ROUNDS": str(max_rounds),
            "RDAGENT_SYMBOLS": ",".join(symbols),
        }

        process = subprocess.Popen(
            ["python", "-m", "rdagent", "quant_factor_experiment"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        return process

    async def _monitor_process(self, market: str) -> None:
        """Monitor the RD-Agent subprocess, parsing stdout for progress.

        Runs as a background asyncio task. Reads stdout line by line,
        extracts round progress and factor discovery events, and updates
        the in-memory task state. On process exit, parses final output
        and registers discovered factors.
        """
        task = self._tasks.get(market)
        process = self._processes.get(market)
        if not task or not process:
            _release_lock(market)
            return

        discovered_factors: list[dict] = []
        start_time = datetime.now()

        try:
            while process.poll() is None:
                # Check for timeout
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > _MAX_RUNTIME_SECONDS:
                    logger.warning(
                        "RD-Agent %s exceeded %ds runtime, killing",
                        market, _MAX_RUNTIME_SECONDS,
                    )
                    process.kill()
                    task.status = "failed"
                    task.error = "Exceeded maximum runtime (24h)"
                    break

                # Read one line (blocking, in thread)
                line = await asyncio.to_thread(self._readline_safe, process)
                if line is None:
                    # stdout closed, process likely exiting
                    await asyncio.sleep(0.5)
                    continue

                line = line.rstrip()
                if not line:
                    continue

                # Store in log buffer
                task.log_lines.append(line)
                if len(task.log_lines) > _MAX_LOG_LINES:
                    task.log_lines = task.log_lines[-_MAX_LOG_LINES:]

                # Parse round progress
                round_match = _RE_ROUND.search(line)
                if round_match:
                    task.current_round = int(round_match.group(1))
                    logger.debug(
                        "RD-Agent %s: round %d/%d",
                        market, task.current_round, task.max_rounds,
                    )

                # Parse factor discovery
                factor_match = _RE_FACTOR_DISCOVERED.search(line)
                if factor_match:
                    factor_info = self._parse_factor_line(
                        line, factor_match, task.current_round,
                    )
                    if factor_info:
                        discovered_factors.append(factor_info)
                        task.discovered_count = len(discovered_factors)
                        logger.info(
                            "RD-Agent %s: factor discovered (total %d)",
                            market, task.discovered_count,
                        )

            # Process exited -- wait for return code
            await asyncio.to_thread(process.wait, timeout=10)
            exit_code = process.returncode

            if task.status not in ("failed", "stopped"):
                if exit_code == 0:
                    task.status = "completed"
                    logger.info(
                        "RD-Agent %s completed: rounds=%d, factors=%d",
                        market, task.current_round, len(discovered_factors),
                    )
                else:
                    task.status = "failed"
                    task.error = f"Process exited with code {exit_code}"
                    logger.error(
                        "RD-Agent %s failed: exit_code=%d",
                        market, exit_code,
                    )

            # Register discovered factors
            if discovered_factors:
                await self._register_factors(market, discovered_factors)

        except asyncio.CancelledError:
            logger.info("RD-Agent monitor cancelled for %s", market)
            task.status = "stopped"
        except Exception as e:
            logger.exception("RD-Agent monitor error for %s: %s", market, e)
            task.status = "failed"
            task.error = str(e)
        finally:
            task.completed_at = datetime.now()
            _release_lock(market)
            # Clean up process reference
            self._processes.pop(market, None)
            self._monitors.pop(market, None)

    @staticmethod
    def _readline_safe(process: subprocess.Popen) -> Optional[str]:
        """Read a line from process stdout, returning None on EOF/error."""
        try:
            if process.stdout is None:
                return None
            line = process.stdout.readline()
            return line if line else None
        except (ValueError, OSError):
            return None

    @staticmethod
    def _parse_factor_line(
        line: str,
        match: re.Match,
        current_round: int,
    ) -> Optional[dict]:
        """Extract factor info from a log line matching the discovery pattern.

        Returns a dict with name, expression, ic, icir, discovery_round
        or None if parsing fails.
        """
        name = match.group(1).strip()

        ic_val = 0.0
        icir_val = 0.0

        if match.group(2):
            try:
                ic_val = float(match.group(2))
            except ValueError:
                pass

        if match.group(3):
            try:
                icir_val = float(match.group(3))
            except ValueError:
                pass

        # Try to extract expression from the same line or use name as fallback
        expr_match = _RE_EXPRESSION.search(line)
        expression = expr_match.group(1).strip() if expr_match else name

        return {
            "name": name,
            "expression": expression,
            "ic": ic_val,
            "icir": icir_val,
            "discovery_round": current_round,
        }

    @staticmethod
    async def _register_factors(market: str, factors: list[dict]) -> None:
        """Register discovered factors via the factor registry."""
        from app.services.factor_registry import factor_registry

        try:
            count = await factor_registry.register_batch(
                factors=factors,
                market=market,
                universe_id=None,
            )
            logger.info(
                "Registered %d/%d discovered factors for market=%s",
                count, len(factors), market,
            )
        except Exception as e:
            logger.error(
                "Failed to register factors for %s: %s", market, e,
            )

    @staticmethod
    async def _resolve_symbols(
        market: str,
        universe_id: str | None,
    ) -> list[str]:
        """Resolve the symbol list for an RD-Agent run.

        First tries the specified universe_id, then falls back to the
        default universe for the market from SettingsCache.

        Args:
            market: Market code.
            universe_id: Optional specific universe UUID.

        Returns:
            List of stock symbols, empty if none found.
        """
        universes = await settings_cache.get_universes(market=market)

        if universe_id:
            for u in universes:
                if str(u.id) == universe_id and u.symbols:
                    logger.info(
                        "Resolved %d symbols from universe %s (%s)",
                        len(u.symbols), u.name, universe_id,
                    )
                    return u.symbols

        # Fall back to default universe
        for u in universes:
            if u.is_default and u.symbols:
                logger.info(
                    "Resolved %d symbols from default universe %s",
                    len(u.symbols), u.name,
                )
                return u.symbols

        # If universes exist but have no explicit symbols (index-type),
        # return empty and let the caller handle it
        if universes:
            logger.warning(
                "Universe(s) found for %s but none have explicit symbols. "
                "Index-type universes require symbol resolution first.",
                market,
            )

        return []

    async def stop(self, market: str) -> dict:
        """Stop a running RD-Agent process.

        Sends SIGTERM, waits up to 30 seconds, then SIGKILL if needed.

        Args:
            market: Market code.

        Returns:
            Updated task status dict, or dict with "error" key.
        """
        async with self._lock:
            process = self._processes.get(market)
            task = self._tasks.get(market)

            if not process or process.poll() is not None:
                return {"error": f"No running RD-Agent process for market {market}"}

            logger.info("Stopping RD-Agent for %s (pid=%d)", market, process.pid)

            if task:
                task.status = "stopped"

            # Send SIGTERM
            try:
                process.send_signal(signal.SIGTERM)
            except OSError as e:
                logger.warning("SIGTERM failed for %s: %s", market, e)

        # Wait for graceful shutdown (outside lock)
        try:
            await asyncio.to_thread(process.wait, timeout=_SIGTERM_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            logger.warning(
                "RD-Agent %s did not exit after %ds, sending SIGKILL",
                market, _SIGTERM_WAIT_SECONDS,
            )
            process.kill()
            await asyncio.to_thread(process.wait, timeout=5)

        # Cancel monitor task
        monitor = self._monitors.get(market)
        if monitor and not monitor.done():
            monitor.cancel()

        # Ensure lock is released
        _release_lock(market)

        if task:
            task.completed_at = datetime.now()
            return task.to_dict()

        return {"market": market, "status": "stopped"}

    async def get_status(self, market: str) -> dict:
        """Get the current status of an RD-Agent task.

        Args:
            market: Market code.

        Returns:
            Task status dict, or idle status if no task exists.
        """
        task = self._tasks.get(market)
        if task:
            return task.to_dict()

        return {
            "market": market,
            "status": "idle",
            "current_round": 0,
            "max_rounds": 0,
            "discovered_count": 0,
            "started_at": None,
            "completed_at": None,
            "error": None,
            "log_tail": [],
        }

    def shutdown(self) -> None:
        """Kill all running RD-Agent processes.

        Called during application shutdown to ensure no orphaned processes.
        """
        for market, process in list(self._processes.items()):
            if process.poll() is None:
                logger.info("Killing RD-Agent process for %s (pid=%d)", market, process.pid)
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception as e:
                    logger.warning("Error killing RD-Agent %s: %s", market, e)

        # Cancel all monitor tasks
        for market, monitor in list(self._monitors.items()):
            if not monitor.done():
                monitor.cancel()

        self._processes.clear()
        self._monitors.clear()
        logger.info("RDAgentRunner shut down")


# Module singleton
rdagent_runner = RDAgentRunner()
