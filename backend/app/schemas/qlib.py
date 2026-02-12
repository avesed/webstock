"""Pydantic schemas for Qlib quantitative analysis endpoints.

Uses CamelModel for automatic snake_case -> camelCase conversion
in API responses, matching the frontend convention.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.models.qlib_backtest import QlibBacktest

import re

from pydantic import Field, field_validator

from app.schemas.base import CamelModel

_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.=\-]{1,20}$")


class QlibMarket(str, Enum):
    US = "us"
    HK = "hk"
    SH = "sh"
    SZ = "sz"
    CN = "cn"
    METAL = "metal"


class QlibAlphaType(str, Enum):
    ALPHA158 = "alpha158"
    ALPHA360 = "alpha360"


class QlibStrategyType(str, Enum):
    TOPK = "topk"
    SIGNAL = "signal"
    LONG_SHORT = "long_short"


class QlibBacktestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# === Expression Engine ===

class ExpressionEvaluateRequest(CamelModel):
    symbol: str = Field(..., max_length=20, pattern=r"^[A-Za-z0-9.=\-]+$")
    expression: str = Field(..., max_length=500)
    market: QlibMarket = QlibMarket.US
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    period: str = "3mo"


class ExpressionBatchRequest(CamelModel):
    symbols: List[str] = Field(..., min_length=1, max_length=500)
    expression: str = Field(..., max_length=500)
    market: QlibMarket = QlibMarket.US
    target_date: Optional[date] = None


class ExpressionBatchResultResponse(CamelModel):
    expression: str
    results: Dict[str, Optional[float]] = Field(default_factory=dict)
    date: Optional[str] = None


class ExpressionValidateRequest(CamelModel):
    expression: str = Field(..., max_length=500)


class ExpressionResultResponse(CamelModel):
    symbol: str
    expression: str
    series: List[Dict[str, Any]] = Field(default_factory=list)
    latest_value: Optional[float] = None
    count: int = 0


class ValidationResultResponse(CamelModel):
    valid: bool
    error: Optional[str] = None
    operators_used: List[str] = Field(default_factory=list)


# === Factors ===

class FactorResultResponse(CamelModel):
    symbol: str
    market: str
    alpha_type: str
    mode: str
    factor_count: int = 0
    dates: List[str] = Field(default_factory=list)
    factors: Dict[str, List[Optional[float]]] = Field(default_factory=dict)
    top_factors: List[Dict[str, Any]] = Field(default_factory=list)


class FactorSummaryResponse(CamelModel):
    symbol: str
    market: str
    latest_date: Optional[str] = None
    top_factors: List[Dict[str, Any]] = Field(default_factory=list)
    mode: str = "single"


class ICAnalysisRequest(CamelModel):
    universe: List[str] = Field(..., min_length=2)
    factor_names: List[str] = Field(default_factory=list)
    market: QlibMarket = QlibMarket.US
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    forward_days: int = Field(5, ge=1, le=20)


class ICAnalysisResponse(CamelModel):
    factor_ic: Dict[str, float] = Field(default_factory=dict)
    factor_icir: Dict[str, float] = Field(default_factory=dict)
    ic_series: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)


# === Backtests ===

class BacktestCreateRequest(CamelModel):
    name: str = Field(..., max_length=200)
    market: QlibMarket = QlibMarket.US
    symbols: List[str] = Field(..., min_length=1, max_length=500)
    start_date: date
    end_date: date
    strategy_type: QlibStrategyType = QlibStrategyType.TOPK
    strategy_config: Dict[str, Any] = Field(default_factory=dict)
    execution_config: Dict[str, Any] = Field(default_factory=dict)


class BacktestEquityPointSchema(CamelModel):
    date: str
    value: float


class BacktestDrawdownPeriodSchema(CamelModel):
    start: Optional[str] = None
    end: Optional[str] = None


class BacktestResultsSchema(CamelModel):
    """Typed schema for backtest results, ensuring snake_case -> camelCase.

    The qlib-service returns results with snake_case keys (e.g. equity_curve,
    total_return). This schema ensures automatic conversion to camelCase for
    the frontend (equityCurve, totalReturn), leveraging CamelModel's
    alias_generator.
    """
    equity_curve: List[BacktestEquityPointSchema] = Field(default_factory=list)
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_period: Optional[BacktestDrawdownPeriodSchema] = None
    annual_volatility: float = 0.0
    calmar_ratio: float = 0.0
    turnover_rate: float = 0.0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0
    total_trades: int = 0
    trading_days: int = 0


class BacktestResponse(CamelModel):
    id: str
    name: str
    market: str
    symbols: List[str] = Field(default_factory=list)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    strategy_type: Optional[str] = None
    strategy_config: Dict[str, Any] = Field(default_factory=dict)
    execution_config: Dict[str, Any] = Field(default_factory=dict)
    status: QlibBacktestStatus = QlibBacktestStatus.PENDING
    progress: int = 0
    results: Optional[BacktestResultsSchema] = None
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @classmethod
    def from_model(cls, bt: "QlibBacktest") -> "BacktestResponse":
        """Build response from the SQLAlchemy model."""
        return cls(
            id=str(bt.id),
            name=bt.name,
            market=bt.market,
            symbols=bt.symbols or [],
            start_date=bt.start_date,
            end_date=bt.end_date,
            strategy_type=bt.strategy_type,
            strategy_config=bt.strategy_config or {},
            execution_config=bt.execution_config or {},
            status=bt.status,
            progress=bt.progress,
            results=bt.results,
            error=bt.error_message,
            created_at=bt.created_at,
            completed_at=bt.completed_at,
        )


class BacktestListResponse(CamelModel):
    items: List[BacktestResponse] = Field(default_factory=list)
    total: int


# === Portfolio Optimization ===

class OptimizationMethod(str, Enum):
    MAX_SHARPE = "max_sharpe"
    MIN_VOLATILITY = "min_volatility"
    RISK_PARITY = "risk_parity"
    EFFICIENT_RETURN = "efficient_return"


class OptimizationConstraints(CamelModel):
    """Typed constraints for portfolio optimization."""
    min_weight: float = Field(0.0, ge=0.0, le=1.0)
    max_weight: float = Field(1.0, ge=0.0, le=1.0)
    risk_free_rate: float = Field(0.02, ge=-0.1, le=0.5)
    target_return: float = Field(0.1, ge=-1.0, le=5.0)


def _validate_symbols(v: List[str]) -> List[str]:
    """Shared symbol list validator."""
    for s in v:
        if not _SYMBOL_PATTERN.match(s):
            raise ValueError(f"Invalid symbol format: {s}")
    return [s.upper() for s in v]


class PortfolioOptimizeRequest(CamelModel):
    symbols: List[str] = Field(..., min_length=2, max_length=100)
    method: OptimizationMethod = OptimizationMethod.MAX_SHARPE
    lookback_days: int = Field(252, ge=30, le=1260)
    constraints: OptimizationConstraints = Field(default_factory=OptimizationConstraints)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
        return _validate_symbols(v)


class PortfolioOptimizeResponse(CamelModel):
    weights: Dict[str, float] = Field(default_factory=dict)
    expected_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    method: str = ""
    symbols: List[str] = Field(default_factory=list)
    data_days: int = 0


class EfficientFrontierRequest(CamelModel):
    symbols: List[str] = Field(..., min_length=2, max_length=100)
    n_points: int = Field(20, ge=5, le=100)
    lookback_days: int = Field(252, ge=30, le=1260)
    constraints: OptimizationConstraints = Field(default_factory=OptimizationConstraints)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
        return _validate_symbols(v)


class FrontierPointResponse(CamelModel):
    expected_return: float
    volatility: float
    sharpe_ratio: float


class EfficientFrontierResponse(CamelModel):
    symbols: List[str] = Field(default_factory=list)
    data_days: int = 0
    frontier: List[FrontierPointResponse] = Field(default_factory=list)


class RiskDecompositionRequest(CamelModel):
    symbols: List[str] = Field(..., min_length=2, max_length=100)
    weights: Dict[str, float] = Field(...)
    lookback_days: int = Field(252, ge=30, le=1260)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, v: List[str]) -> List[str]:
        return _validate_symbols(v)


class AssetRiskContribution(CamelModel):
    weight: float
    risk_contribution: float
    risk_pct: float


class RiskDecompositionResponse(CamelModel):
    portfolio_volatility: float = 0.0
    contributions: Dict[str, AssetRiskContribution] = Field(default_factory=dict)
    symbols: List[str] = Field(default_factory=list)
    data_days: int = 0


# === Data Sync ===

class DataSyncStatusResponse(CamelModel):
    markets: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


# === Service Status ===

class QlibServiceStatusResponse(CamelModel):
    available: bool
    qlib_initialized: Optional[bool] = None
    qlib_region: Optional[str] = None
    error: Optional[str] = None
