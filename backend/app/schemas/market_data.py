"""Pydantic schemas for market data from yfinance and AKShare."""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.schemas.base import CamelModel


# ============ Enums ============


class DataSourceType(str, Enum):
    """Data source types."""

    YFINANCE = "yfinance"
    AKSHARE = "akshare"
    MIXED = "mixed"


# ============ Institutional Holdings (yfinance) ============


class InstitutionalHolder(CamelModel):
    """
    Single institutional holder from yfinance.

    Source: ticker.institutional_holders DataFrame
    Columns: ['Date Reported', 'Holder', 'pctHeld', 'Shares', 'Value', 'pctChange']
    """

    date_reported: Optional[str] = Field(None, description="报告日期")
    holder: str = Field(..., description="机构名称")
    pct_held: Optional[float] = Field(None, description="持股比例 (0.0-1.0)")
    shares: Optional[int] = Field(None, description="持股数量")
    value: Optional[int] = Field(None, description="持股市值 (USD)")
    pct_change: Optional[float] = Field(None, description="持仓变化比例")


class InstitutionalHoldingsResponse(CamelModel):
    """Response for institutional holdings data."""

    symbol: str
    holders: List[InstitutionalHolder] = Field(default_factory=list)
    total_institutional_pct: Optional[float] = Field(
        None, description="机构总持股比例"
    )
    data_as_of: Optional[str] = Field(None, description="数据截止日期")
    source: DataSourceType = DataSourceType.YFINANCE


# ============ Sector/Industry (yfinance info) ============


class SectorIndustryInfo(CamelModel):
    """
    Sector and industry classification from yfinance.

    Source: ticker.info['sector'], ticker.info['industry']
    """

    symbol: str
    sector: Optional[str] = Field(None, description="行业大类 (e.g., Technology)")
    industry: Optional[str] = Field(
        None, description="细分行业 (e.g., Consumer Electronics)"
    )
    source: DataSourceType = DataSourceType.YFINANCE


# ============ Market Index Data (yfinance) ============


class IndexOHLCV(CamelModel):
    """Single OHLCV bar for index."""

    date: str = Field(..., description="日期 ISO format")
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketIndexData(CamelModel):
    """
    Market index historical data.

    Supported indices (via yfinance):
    - ^GSPC (S&P 500)
    - ^HSI (Hang Seng Index)
    - 000001.SS (Shanghai Composite)
    - 399001.SZ (Shenzhen Component)
    """

    symbol: str = Field(
        ..., description="指数代码 (^GSPC, ^HSI, 000001.SS, 399001.SZ)"
    )
    name: str = Field(..., description="指数名称")
    bars: List[IndexOHLCV] = Field(default_factory=list)
    latest_close: Optional[float] = None
    change_pct: Optional[float] = Field(None, description="涨跌幅 %")
    source: DataSourceType = DataSourceType.YFINANCE


# ============ Fund Holdings (AKShare - A股基金持仓) ============


class FundHoldingItem(CamelModel):
    """
    Single stock fund holding summary from AKShare.

    Source: ak.stock_institute_hold(symbol='20243')
    Columns: ['证券代码', '证券简称', '机构数', '机构数变化', '持股比例',
              '持股比例增幅', '占流通股比例', '占流通股比例增幅']

    Note: Returns ALL stocks, must filter by stock code.
    """

    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票简称")
    institution_count: int = Field(..., description="持仓机构数量")
    institution_count_change: Optional[int] = Field(None, description="机构数变化")
    holding_pct: Optional[float] = Field(None, description="持股比例 %")
    holding_pct_change: Optional[float] = Field(None, description="持股比例变化 %")
    float_pct: Optional[float] = Field(None, description="占流通股比例 %")
    float_pct_change: Optional[float] = Field(None, description="占流通股比例变化 %")


class FundHoldingsResponse(CamelModel):
    """Response for A-share fund holdings."""

    symbol: str
    quarter: str = Field(..., description="季度 (e.g., '20243' = 2024年Q3)")
    holdings: Optional[FundHoldingItem] = Field(
        None, description="该股票的基金持仓汇总"
    )
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Northbound Individual Stock Holding (AKShare) ============


class NorthboundStockHolding(CamelModel):
    """
    Individual stock northbound holding from AKShare.

    Source: ak.stock_hsgt_individual_em(symbol='600519')
    Columns: ['持股日期', '当日收盘价', '当日涨跌幅', '持股数量', '持股市值',
              '持股数量占A股百分比', '今日增持股数', '今日增持资金', '今日持股市值变化']

    WARNING: Data after 2024-08-16 may not be available due to policy change.
    """

    holding_date: str = Field(..., description="持股日期")
    close_price: Optional[float] = Field(None, description="当日收盘价")
    change_pct: Optional[float] = Field(None, description="当日涨跌幅 %")
    holding_shares: int = Field(..., description="持股数量")
    holding_value: float = Field(..., description="持股市值 (元)")
    holding_pct: Optional[float] = Field(None, description="持股数量占A股百分比 %")
    change_shares: Optional[float] = Field(None, description="今日增持股数")
    change_value: Optional[float] = Field(None, description="今日增持资金 (元)")
    value_change: Optional[float] = Field(None, description="今日持股市值变化 (元)")


class NorthboundHoldingResponse(CamelModel):
    """Response for individual stock northbound holding."""

    symbol: str
    stock_name: Optional[str] = None
    holdings: List[NorthboundStockHolding] = Field(default_factory=list)
    latest_holding: Optional[NorthboundStockHolding] = Field(
        None, description="最新持仓数据"
    )
    data_cutoff_notice: str = Field(
        default="数据可能在 2024-08-16 后不更新，请以交易所公告为准",
        description="数据截止提示",
    )
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Northbound Capital Flow (AKShare) ============


class NorthboundFlowDaily(CamelModel):
    """
    Daily northbound capital flow.

    Source: ak.stock_hsgt_hist_em(symbol='北向资金')
    Columns: ['日期', '当日成交净买额', '买入成交额', '卖出成交额',
              '历史累计净买额', '当日资金流入', '当日余额', '持股市值', ...]

    WARNING: Data after 2024-08-19 shows NaN values - API limitation.
    """

    date: str = Field(..., description="日期 YYYY-MM-DD")
    net_buy: Optional[float] = Field(None, description="当日成交净买额 (亿元)")
    buy_amount: Optional[float] = Field(None, description="买入成交额 (亿元)")
    sell_amount: Optional[float] = Field(None, description="卖出成交额 (亿元)")
    cumulative_net_buy: Optional[float] = Field(
        None, description="历史累计净买额 (亿元)"
    )
    inflow: Optional[float] = Field(None, description="当日资金流入 (亿元)")
    remaining_quota: Optional[float] = Field(None, description="当日余额 (亿元)")
    holding_value: Optional[float] = Field(None, description="持股市值 (亿元)")


class NorthboundFlowResponse(CamelModel):
    """Response for northbound capital flow history."""

    direction: str = Field(..., description="北向资金/沪股通/深股通")
    flows: List[NorthboundFlowDaily] = Field(default_factory=list)
    latest_valid_date: Optional[str] = Field(
        None, description="最新有效数据日期"
    )
    data_cutoff_notice: str = Field(
        default="数据可能在 2024-08-19 后不完整，请以交易所公告为准",
        description="数据截止提示",
    )
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Industry Sector Data (AKShare) ============


class IndustrySectorInfo(CamelModel):
    """
    Industry sector from AKShare.

    Source: ak.stock_board_industry_name_em()
    Columns: ['排名', '板块名称', '板块代码', '最新价', '涨跌额', '涨跌幅',
              '总市值', '换手率', '上涨家数', '下跌家数', '领涨股票', '领涨股票-涨跌幅']
    """

    rank: int = Field(..., description="排名")
    sector_name: str = Field(..., description="板块名称")
    sector_code: str = Field(..., description="板块代码")
    latest_price: Optional[float] = Field(None, description="最新价")
    change: Optional[float] = Field(None, description="涨跌额")
    change_pct: Optional[float] = Field(None, description="涨跌幅 %")
    total_market_cap: Optional[float] = Field(None, description="总市值")
    turnover_rate: Optional[float] = Field(None, description="换手率 %")
    up_count: Optional[int] = Field(None, description="上涨家数")
    down_count: Optional[int] = Field(None, description="下跌家数")
    leading_stock: Optional[str] = Field(None, description="领涨股票")
    leading_stock_change: Optional[float] = Field(None, description="领涨股票涨跌幅 %")


class IndustrySectorListResponse(CamelModel):
    """Response for industry sector list."""

    sectors: List[IndustrySectorInfo] = Field(default_factory=list)
    update_time: Optional[str] = None
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Stock Individual Info CN (AKShare) ============


class StockIndividualInfoCN(CamelModel):
    """
    Individual stock info from AKShare for A-shares.

    Source: ak.stock_individual_info_em(symbol='600519')
    Returns DataFrame with columns: ['item', 'value']
    Key items: '股票代码', '股票简称', '行业', '总市值', '流通市值', etc.
    """

    stock_code: str = Field(..., description="股票代码")
    stock_name: str = Field(..., description="股票简称")
    industry: Optional[str] = Field(None, description="所属行业板块 (e.g., '酿酒行业')")
    total_market_cap: Optional[float] = Field(None, description="总市值 (元)")
    float_market_cap: Optional[float] = Field(None, description="流通市值 (元)")
    total_shares: Optional[float] = Field(None, description="总股本")
    float_shares: Optional[float] = Field(None, description="流通股本")
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Sector Historical Data (AKShare) ============


class SectorHistoryBar(CamelModel):
    """
    Single OHLCV bar for sector index.

    Source: ak.stock_board_industry_hist_em(symbol='酿酒行业', period='日k', ...)
    Columns: ['日期', '开盘', '收盘', '最高', '最低', '涨跌幅', '涨跌额',
              '成交量', '成交额', '振幅', '换手率']
    """

    date: str = Field(..., description="日期")
    open: float
    close: float
    high: float
    low: float
    change_pct: Optional[float] = Field(None, description="涨跌幅 %")
    change: Optional[float] = Field(None, description="涨跌额")
    volume: Optional[int] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")
    amplitude: Optional[float] = Field(None, description="振幅 %")
    turnover_rate: Optional[float] = Field(None, description="换手率 %")


class SectorHistoryResponse(CamelModel):
    """Response for sector historical data."""

    sector_name: str = Field(..., description="板块名称 (e.g., '酿酒行业')")
    period: str = Field(default="日k", description="周期: 日k/周k/月k")
    bars: List[SectorHistoryBar] = Field(default_factory=list)
    source: DataSourceType = DataSourceType.AKSHARE


# ============ Aggregated Market Context ============


class NorthboundSummary(CamelModel):
    """Summary of northbound capital flow."""

    latest_date: Optional[str] = Field(None, description="最新数据日期")
    latest_net_buy: Optional[float] = Field(None, description="最新日净买入 (亿元)")
    last_5d_net_buy: Optional[float] = Field(None, description="近5日净买入 (亿元)")
    cumulative_net_buy: Optional[float] = Field(None, description="历史累计净买入 (亿元)")
    data_cutoff_notice: Optional[str] = None


class MarketContext(CamelModel):
    """
    Aggregated market context for sentiment analysis.

    Combines multiple data sources to provide market overview.
    """

    # Market indices
    sp500: Optional[MarketIndexData] = Field(None, description="S&P 500")
    hang_seng: Optional[MarketIndexData] = Field(None, description="恒生指数")
    shanghai_composite: Optional[MarketIndexData] = Field(None, description="上证综指")
    shenzhen_component: Optional[MarketIndexData] = Field(None, description="深证成指")

    # Northbound flow summary (latest valid data)
    northbound_summary: Optional[NorthboundSummary] = Field(
        None, description="北向资金汇总"
    )

    # Timestamp
    fetched_at: str = Field(
        default_factory=lambda: datetime.utcnow().isoformat(),
        description="数据获取时间",
    )
    source: DataSourceType = DataSourceType.MIXED


# ============ Error Response ============


class MarketDataError(CamelModel):
    """Error response for market data operations."""

    error: str
    error_code: Optional[str] = None
    source: Optional[DataSourceType] = None
    symbol: Optional[str] = None
    recoverable: bool = Field(default=True, description="是否可通过重试恢复")
    fallback_available: bool = Field(default=False, description="是否有降级数据可用")
