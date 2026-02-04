"""Circuit breaker pattern for fault tolerance."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation, requests pass through
    OPEN = "open"  # Circuit is open, requests fail immediately
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5  # Number of failures before opening
    recovery_timeout: float = 60.0  # Seconds before attempting recovery
    half_open_max_calls: int = 1  # Max concurrent calls in half-open state


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker."""

    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    consecutive_failures: int = 0
    consecutive_successes: int = 0


class CircuitBreaker:
    """
    Circuit breaker implementation for protecting against cascading failures.

    States:
    - CLOSED: Normal operation. Failures are counted. If failure_threshold
      is reached, transitions to OPEN.
    - OPEN: All requests are rejected immediately. After recovery_timeout,
      transitions to HALF_OPEN.
    - HALF_OPEN: Limited requests are allowed through to test if the service
      has recovered. Success transitions to CLOSED, failure transitions to OPEN.

    Usage:
        circuit = CircuitBreaker(name="openai", config=CircuitBreakerConfig())

        async def call_api():
            return await circuit.call(some_async_function, arg1, arg2)
    """

    def __init__(
        self,
        name: str,
        config: Optional[CircuitBreakerConfig] = None,
    ):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()
        self._opened_at: Optional[float] = None
        self._half_open_calls: int = 0
        # Semaphore to limit concurrent calls in HALF_OPEN state
        # This prevents race conditions where multiple calls pass through
        # before state transitions are recorded
        self._half_open_semaphore = asyncio.Semaphore(
            config.half_open_max_calls if config else 1
        )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state

    @property
    def stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics."""
        return self._stats

    @property
    def is_closed(self) -> bool:
        """Check if circuit is closed (normal operation)."""
        return self._state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self._state == CircuitState.OPEN

    @property
    def is_half_open(self) -> bool:
        """Check if circuit is half-open (testing recovery)."""
        return self._state == CircuitState.HALF_OPEN

    async def _check_state_transition(self) -> None:
        """Check if state should transition based on current conditions."""
        now = time.time()

        if self._state == CircuitState.OPEN:
            # Check if recovery timeout has passed
            if self._opened_at and (now - self._opened_at) >= self.config.recovery_timeout:
                logger.info(
                    f"Circuit breaker '{self.name}' transitioning from OPEN to HALF_OPEN "
                    f"after {self.config.recovery_timeout}s recovery timeout"
                )
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0

    def _record_success(self) -> None:
        """Record a successful call."""
        self._stats.total_calls += 1
        self._stats.successful_calls += 1
        self._stats.last_success_time = time.time()
        self._stats.consecutive_successes += 1
        self._stats.consecutive_failures = 0

        if self._state == CircuitState.HALF_OPEN:
            # Successful call in half-open state, close the circuit
            logger.info(
                f"Circuit breaker '{self.name}' transitioning from HALF_OPEN to CLOSED "
                "after successful recovery"
            )
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def _record_failure(self, error: Exception) -> None:
        """Record a failed call."""
        self._stats.total_calls += 1
        self._stats.failed_calls += 1
        self._stats.last_failure_time = time.time()
        self._stats.consecutive_failures += 1
        self._stats.consecutive_successes = 0

        logger.warning(
            f"Circuit breaker '{self.name}' recorded failure "
            f"({self._stats.consecutive_failures}/{self.config.failure_threshold}): {error}"
        )

        if self._state == CircuitState.CLOSED:
            # Check if we should open the circuit
            if self._stats.consecutive_failures >= self.config.failure_threshold:
                logger.error(
                    f"Circuit breaker '{self.name}' transitioning to OPEN "
                    f"after {self._stats.consecutive_failures} consecutive failures"
                )
                self._state = CircuitState.OPEN
                self._opened_at = time.time()

        elif self._state == CircuitState.HALF_OPEN:
            # Failure in half-open state, re-open the circuit
            logger.warning(
                f"Circuit breaker '{self.name}' transitioning back to OPEN "
                "after failure in HALF_OPEN state"
            )
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    def _record_rejection(self) -> None:
        """Record a rejected call (circuit open)."""
        self._stats.total_calls += 1
        self._stats.rejected_calls += 1

    async def call(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute a function through the circuit breaker.

        Args:
            func: Async function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            Result of the function call

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Any exception raised by the function
        """
        # Determine the current state and whether we can proceed
        async with self._lock:
            await self._check_state_transition()

            if self._state == CircuitState.OPEN:
                self._record_rejection()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is OPEN. "
                    f"Retry after {self.config.recovery_timeout}s"
                )

            current_state = self._state

        # For HALF_OPEN state, use semaphore to limit concurrent calls
        # This prevents race conditions where multiple calls could pass through
        # before the first one completes and updates the state
        if current_state == CircuitState.HALF_OPEN:
            # Try to acquire semaphore without waiting
            acquired = self._half_open_semaphore.locked()
            if acquired:
                # Semaphore is already at capacity, reject this call
                async with self._lock:
                    self._record_rejection()
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{self.name}' is HALF_OPEN with max calls in progress"
                )

            # Execute with semaphore held
            async with self._half_open_semaphore:
                return await self._execute_call(func, *args, **kwargs)
        else:
            # CLOSED state - execute normally without semaphore
            return await self._execute_call(func, *args, **kwargs)

    async def _execute_call(
        self,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute the actual function call with proper state recording."""
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            async with self._lock:
                self._record_success()

            return result

        except Exception as e:
            async with self._lock:
                self._record_failure(e)
            raise

    async def reset(self) -> None:
        """Reset the circuit breaker to closed state."""
        async with self._lock:
            logger.info(f"Circuit breaker '{self.name}' manually reset to CLOSED")
            self._state = CircuitState.CLOSED
            self._stats = CircuitBreakerStats()
            self._opened_at = None
            self._half_open_calls = 0
            # Reset semaphore by creating a new one
            self._half_open_semaphore = asyncio.Semaphore(
                self.config.half_open_max_calls
            )

    def get_status(self) -> dict:
        """Get circuit breaker status as a dictionary."""
        return {
            "name": self.name,
            "state": self._state.value,
            "stats": {
                "total_calls": self._stats.total_calls,
                "successful_calls": self._stats.successful_calls,
                "failed_calls": self._stats.failed_calls,
                "rejected_calls": self._stats.rejected_calls,
                "consecutive_failures": self._stats.consecutive_failures,
                "consecutive_successes": self._stats.consecutive_successes,
            },
            "config": {
                "failure_threshold": self.config.failure_threshold,
                "recovery_timeout": self.config.recovery_timeout,
                "half_open_max_calls": self.config.half_open_max_calls,
            },
        }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open."""

    pass


# Global circuit breaker registry
_circuit_breakers: dict[str, CircuitBreaker] = {}
_registry_lock = asyncio.Lock()


async def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """
    Get or create a circuit breaker by name.

    Args:
        name: Unique identifier for the circuit breaker
        config: Configuration (only used when creating new breaker)

    Returns:
        CircuitBreaker instance
    """
    async with _registry_lock:
        if name not in _circuit_breakers:
            _circuit_breakers[name] = CircuitBreaker(name, config)
        return _circuit_breakers[name]


async def reset_all_circuit_breakers() -> None:
    """Reset all circuit breakers to closed state."""
    async with _registry_lock:
        for breaker in _circuit_breakers.values():
            await breaker.reset()


def get_all_circuit_breaker_status() -> list[dict]:
    """Get status of all circuit breakers."""
    return [breaker.get_status() for breaker in _circuit_breakers.values()]
