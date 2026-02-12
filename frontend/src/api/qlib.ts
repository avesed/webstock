import apiClient from './client'

// --- Types ---

export interface QlibTopFactor {
  name: string
  value: number
  zScore: number
}

export interface QlibFactorSummary {
  symbol: string
  market: string
  latestDate: string | null
  topFactors: QlibTopFactor[]
  mode: string
}

export interface QlibFactorResult {
  symbol: string
  market: string
  alphaType: string
  mode: string
  factorCount: number
  dates: string[]
  factors: Record<string, (number | null)[]>
  topFactors: QlibTopFactor[]
}

export interface QlibServiceStatus {
  available: boolean
  qlibInitialized?: boolean
  qlibRegion?: string
  error?: string
}

// --- Backtest Types ---

export type BacktestStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
export type StrategyType = 'topk' | 'signal' | 'long_short'

export interface BacktestCreateRequest {
  name: string
  market: string
  symbols: string[]
  startDate: string
  endDate: string
  strategyType: StrategyType
  strategyConfig: Record<string, unknown>
  executionConfig?: Record<string, unknown>
}

export interface BacktestEquityPoint {
  date: string
  value: number
}

export interface BacktestDrawdownPeriod {
  start: string
  end: string
}

export interface BacktestResults {
  equityCurve: BacktestEquityPoint[]
  totalReturn: number
  annualReturn: number
  sharpeRatio: number
  sortinoRatio: number
  maxDrawdown: number
  maxDrawdownPeriod?: BacktestDrawdownPeriod
  annualVolatility: number
  calmarRatio: number
  turnoverRate: number
  winRate: number
  profitLossRatio: number
  totalTrades: number
  tradingDays: number
}

export interface BacktestResponse {
  id: string
  name: string
  market: string
  symbols: string[]
  startDate: string
  endDate: string
  strategyType: StrategyType
  strategyConfig: Record<string, unknown>
  status: BacktestStatus
  progress: number
  results?: BacktestResults
  error?: string
  createdAt: string
  completedAt?: string
}

// --- Portfolio Optimization Types ---

export type OptimizationMethod = 'max_sharpe' | 'min_volatility' | 'risk_parity' | 'efficient_return'

export interface PortfolioOptimizeRequest {
  symbols: string[]
  method: OptimizationMethod
  lookbackDays: number
  constraints: Record<string, unknown>
}

export interface PortfolioOptimizeResponse {
  weights: Record<string, number>
  expectedReturn: number
  annualVolatility: number
  sharpeRatio: number
  method: string
  symbols: string[]
  dataDays: number
}

export interface EfficientFrontierRequest {
  symbols: string[]
  nPoints: number
  lookbackDays: number
  constraints: Record<string, unknown>
}

export interface FrontierPoint {
  expectedReturn: number
  volatility: number
  sharpeRatio: number
}

export interface EfficientFrontierResponse {
  symbols: string[]
  dataDays: number
  frontier: FrontierPoint[]
}

export interface RiskDecompositionRequest {
  symbols: string[]
  weights: Record<string, number>
  lookbackDays: number
}

export interface AssetRiskContribution {
  weight: number
  riskContribution: number
  riskPct: number
}

export interface RiskDecompositionResponse {
  portfolioVolatility: number
  contributions: Record<string, AssetRiskContribution>
  symbols: string[]
  dataDays: number
}

// --- API Functions ---

export const qlibApi = {
  /**
   * Check if the Qlib service is available and initialized.
   */
  getStatus: async (): Promise<QlibServiceStatus> => {
    const response = await apiClient.get<QlibServiceStatus>('/qlib/status')
    return response.data
  },

  /**
   * Fetch full factor data for a stock symbol.
   * @param symbol - Stock symbol (e.g., "AAPL")
   * @param market - Market identifier (default: "us")
   * @param alphaType - Factor set type (default: "alpha158")
   */
  getFactors: async (
    symbol: string,
    market = 'us',
    alphaType = 'alpha158',
  ): Promise<QlibFactorResult> => {
    const response = await apiClient.get<QlibFactorResult>(
      `/qlib/factors/${encodeURIComponent(symbol)}`,
      { params: { market, alpha_type: alphaType } },
    )
    return response.data
  },

  /**
   * Fetch a summary of top factors for a stock symbol.
   * @param symbol - Stock symbol (e.g., "AAPL")
   * @param market - Market identifier (default: "us")
   */
  getFactorSummary: async (
    symbol: string,
    market = 'us',
  ): Promise<QlibFactorSummary> => {
    const response = await apiClient.get<QlibFactorSummary>(
      `/qlib/factors/${encodeURIComponent(symbol)}/summary`,
      { params: { market } },
    )
    return response.data
  },

  // --- Backtest API ---

  /**
   * Create a new backtest.
   */
  createBacktest: async (request: BacktestCreateRequest): Promise<BacktestResponse> => {
    const response = await apiClient.post<BacktestResponse>('/qlib/backtests', request)
    return response.data
  },

  /**
   * List all backtests for the current user.
   * @param limit - Max items to return (default: 20)
   * @param offset - Offset for pagination (default: 0)
   */
  getBacktests: async (limit = 20, offset = 0): Promise<BacktestResponse[]> => {
    const response = await apiClient.get<{ items: BacktestResponse[]; total: number }>('/qlib/backtests', {
      params: { limit, offset },
    })
    return response.data.items
  },

  /**
   * Get a single backtest by ID.
   */
  getBacktest: async (id: string): Promise<BacktestResponse> => {
    const response = await apiClient.get<BacktestResponse>(
      `/qlib/backtests/${encodeURIComponent(id)}`,
    )
    return response.data
  },

  /**
   * Cancel a running backtest.
   */
  cancelBacktest: async (id: string): Promise<BacktestResponse> => {
    const response = await apiClient.post<BacktestResponse>(
      `/qlib/backtests/${encodeURIComponent(id)}/cancel`,
    )
    return response.data
  },

  /**
   * Delete a backtest.
   */
  deleteBacktest: async (id: string): Promise<void> => {
    await apiClient.delete(`/qlib/backtests/${encodeURIComponent(id)}`)
  },

  // --- Portfolio Optimization API ---

  /**
   * Optimize a portfolio given symbols and a method.
   */
  optimizePortfolio: async (request: PortfolioOptimizeRequest): Promise<PortfolioOptimizeResponse> => {
    const response = await apiClient.post<PortfolioOptimizeResponse>('/qlib/portfolio/optimize', request)
    return response.data
  },

  /**
   * Compute the efficient frontier for a set of symbols.
   */
  getEfficientFrontier: async (request: EfficientFrontierRequest): Promise<EfficientFrontierResponse> => {
    const response = await apiClient.post<EfficientFrontierResponse>('/qlib/portfolio/efficient-frontier', request)
    return response.data
  },

  /**
   * Get risk decomposition for a portfolio with given weights.
   */
  getRiskDecomposition: async (request: RiskDecompositionRequest): Promise<RiskDecompositionResponse> => {
    const response = await apiClient.post<RiskDecompositionResponse>('/qlib/portfolio/risk-decomposition', request)
    return response.data
  },
}
