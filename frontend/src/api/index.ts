import apiClient from './client'
import { getAccessToken } from '@/lib/auth'
import type {
  AuthTokens,
  LoginCredentials,
  RegisterCredentials,
  User,
  StockQuote,
  StockInfo,
  StockFinancials,
  CandlestickData,
  ChartTimeframe,
  TechnicalIndicatorsData,
  Watchlist,
  Portfolio,
  Transaction,
  Alert,
  CreateAlertInput,
  ReportSchedule,
  Report,
  NewsArticle,
  SentimentTimelineResponse,
  SearchResult,
  PaginatedResponse,
  ChatConversation,
  ChatMessage,
  ChatConversationList,
  ChatStreamEvent,
  RegisterResponse,
  PendingApprovalResponse,
  CheckStatusResponse,
} from '@/types'

/**
 * Convert an ISO datetime string to a Unix timestamp that displays as market-local time.
 * lightweight-charts renders Unix timestamps as UTC, so we shift by the timezone offset
 * embedded in the ISO string so the chart shows the correct local market time.
 * e.g. "2026-02-03T09:30:00-05:00" → displays as 09:30 (not 14:30 UTC)
 */
function toMarketLocalTimestamp(isoDate: string): number {
  const d = new Date(isoDate)
  const utcSeconds = Math.floor(d.getTime() / 1000)

  // Extract timezone offset from ISO string (e.g., "-05:00" or "+08:00")
  const match = isoDate.match(/([+-])(\d{2}):(\d{2})$/)
  if (match) {
    const sign = match[1] === '+' ? 1 : -1
    const hours = parseInt(match[2]!, 10)
    const minutes = parseInt(match[3]!, 10)
    const offsetSeconds = sign * (hours * 3600 + minutes * 60)
    return utcSeconds + offsetSeconds
  }

  // No offset in string (e.g. "Z" or bare) — fall back to UTC
  return utcSeconds
}

/**
 * Convert a datetime string (e.g. "YYYY-MM-DD HH:MM:SS" or ISO with offset) to a
 * Unix timestamp suitable for lightweight-charts. For strings with timezone offset,
 * delegates to toMarketLocalTimestamp. For bare datetime strings (no offset), parses
 * as UTC and returns the seconds value directly (lightweight-charts renders as UTC).
 */
function datetimeToTimestamp(dateStr: string): number {
  // If the string contains a timezone offset, use the existing helper
  if (/[+-]\d{2}:\d{2}$/.test(dateStr) || dateStr.endsWith('Z')) {
    return toMarketLocalTimestamp(dateStr)
  }
  // Bare datetime like "2026-02-03 09:30:00" — parse as UTC
  const d = new Date(dateStr.replace(' ', 'T') + 'Z')
  return Math.floor(d.getTime() / 1000)
}

/**
 * In-place conversion of indicator data point times from datetime strings to
 * Unix timestamps, for use with lightweight-charts in time-based (intraday) mode.
 */
function convertIndicatorTimesToTimestamps(data: TechnicalIndicatorsData): void {
  const convertPoints = (points: Array<{ time: string | number; value: number }>) => {
    for (const p of points) {
      if (typeof p.time === 'string') {
        p.time = datetimeToTimestamp(p.time)
      }
    }
  }

  // MA series (keyed by period, e.g. "20", "50", "200")
  if (data.ma) {
    for (const maData of Object.values(data.ma)) {
      convertPoints(maData.series)
    }
  }

  // RSI
  if (data.rsi) {
    convertPoints(data.rsi.series)
  }

  // MACD
  if (data.macd) {
    convertPoints(data.macd.macdLine)
    convertPoints(data.macd.signalLine)
    convertPoints(data.macd.histogram)
  }

  // Bollinger Bands
  if (data.bb) {
    convertPoints(data.bb.upper)
    convertPoints(data.bb.middle)
    convertPoints(data.bb.lower)
  }
}

// Auth API
export const authApi = {
  login: async (credentials: LoginCredentials): Promise<AuthTokens | PendingApprovalResponse> => {
    const response = await apiClient.post<AuthTokens | PendingApprovalResponse>('/auth/login', credentials)
    return response.data
  },

  register: async (credentials: RegisterCredentials): Promise<RegisterResponse> => {
    const response = await apiClient.post<RegisterResponse>('/auth/register', credentials)
    return response.data
  },

  logout: async (): Promise<void> => {
    await apiClient.post('/auth/logout')
  },

  refresh: async (): Promise<AuthTokens> => {
    const response = await apiClient.post<AuthTokens>('/auth/refresh')
    return response.data
  },

  me: async (token: string): Promise<User> => {
    // 必须提供 token，使用 fetch 直接调用
    const response = await fetch('/api/v1/auth/me', {
      method: 'GET',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`,
      },
    })
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    return response.json()
  },

  checkAccountStatus: async (email: string, pendingToken: string): Promise<CheckStatusResponse> => {
    const response = await apiClient.post<CheckStatusResponse>('/auth/check-status', {
      email,
      pendingToken,
    })
    return response.data
  },
}

// Period mapping: timeframe → yfinance period parameter
const TIMEFRAME_PERIOD: Record<ChartTimeframe, string> = {
  '1H': '1d',
  '1D': '1d',
  '1W': '5d',
  '1M': '1mo',
  '3M': '3mo',
  '6M': '6mo',
  '1Y': '1y',
  '5Y': '5y',
  'ALL': 'max',
}

// Default interval for each timeframe (used when no override is provided)
export const TIMEFRAME_DEFAULT_INTERVAL: Record<ChartTimeframe, string> = {
  '1H': '1m',
  '1D': '5m',
  '1W': '15m',
  '1M': '1h',
  '3M': '1d',
  '6M': '1d',
  '1Y': '1d',
  '5Y': '1wk',
  'ALL': '1mo',
}

// Maximum lookback in days for each interval (yfinance limits)
export const MAX_LOOKBACK_DAYS: Record<string, number> = {
  '1m': 7,
  '2m': 60,
  '5m': 60,
  '15m': 60,
  '30m': 60,
  '1h': 730,
  '1d': Infinity,
  '1wk': Infinity,
  '1mo': Infinity,
}

/** Set of intervals considered intraday (sub-daily) */
const INTRADAY_INTERVALS = new Set(['1m', '2m', '5m', '15m', '30m', '1h'])

/**
 * Clamp a yfinance period to fit within an interval's maximum lookback window.
 * E.g. interval=2m supports max 60 days → period '1y' is clamped to '1mo'.
 */
const PERIOD_APPROX_DAYS: [string, number][] = [
  ['1d', 1],
  ['5d', 5],
  ['1mo', 31],
  ['3mo', 92],
  ['6mo', 183],
  ['1y', 366],
  ['2y', 731],
  ['5y', 1827],
  ['max', Infinity],
]

function clampPeriod(period: string, interval: string): string {
  const maxDays = MAX_LOOKBACK_DAYS[interval]
  if (maxDays == null || !isFinite(maxDays)) return period

  // Check if the current period already fits
  const currentDays = PERIOD_APPROX_DAYS.find(([p]) => p === period)?.[1] ?? Infinity
  if (currentDays <= maxDays) return period

  // Find the largest period that fits within the lookback limit
  let best = '1d'
  for (const [p, days] of PERIOD_APPROX_DAYS) {
    if (days <= maxDays) best = p
    else break
  }
  return best
}

// Stock API
// Use query parameter routes for all symbols to handle special characters (e.g., GC=F for gold futures)
export const stockApi = {
  getQuote: async (symbol: string): Promise<StockQuote> => {
    const response = await apiClient.get<StockQuote>('/stocks/quote', {
      params: { symbol },
    })
    return response.data
  },

  getInfo: async (symbol: string): Promise<StockInfo> => {
    const response = await apiClient.get<StockInfo>('/stocks/info', {
      params: { symbol },
    })
    return response.data
  },

  getFinancials: async (symbol: string): Promise<StockFinancials> => {
    interface FinancialsResponse {
      symbol: string
      peRatio?: number
      forwardPe?: number
      eps?: number
      dividendYield?: number
      dividendRate?: number
      bookValue?: number
      priceToBook?: number
      revenue?: number
      revenueGrowth?: number
      netIncome?: number
      profitMargin?: number
      grossMargin?: number
      operatingMargin?: number
      roe?: number
      roa?: number
      debtToEquity?: number
      currentRatio?: number
      epsGrowth?: number
      payoutRatio?: number
      market: string
      source: string
    }

    const response = await apiClient.get<FinancialsResponse>('/stocks/financials', {
      params: { symbol },
    })
    const data = response.data

    // Map backend fields to frontend StockFinancials interface
    // Only include fields that have values to satisfy exactOptionalPropertyTypes
    const result: StockFinancials = { symbol: data.symbol }

    if (data.peRatio != null) result.peRatio = data.peRatio
    if (data.priceToBook != null) result.pbRatio = data.priceToBook
    if (data.eps != null) result.eps = data.eps
    if (data.epsGrowth != null) result.epsGrowth = data.epsGrowth
    if (data.revenue != null) result.revenue = data.revenue
    if (data.revenueGrowth != null) result.revenueGrowth = data.revenueGrowth
    if (data.netIncome != null) result.netIncome = data.netIncome
    if (data.profitMargin != null) result.netMargin = data.profitMargin
    if (data.grossMargin != null) result.grossMargin = data.grossMargin
    if (data.operatingMargin != null) result.operatingMargin = data.operatingMargin
    if (data.roe != null) result.roe = data.roe
    if (data.roa != null) result.roa = data.roa
    if (data.debtToEquity != null) result.debtToEquity = data.debtToEquity
    if (data.currentRatio != null) result.currentRatio = data.currentRatio
    if (data.dividendYield != null) result.dividendYield = data.dividendYield
    if (data.payoutRatio != null) result.payoutRatio = data.payoutRatio

    return result
  },

  getHistory: async (
    symbol: string,
    timeframe: ChartTimeframe = '1M',
    options?: {
      intervalOverride?: string
      start?: string
      end?: string
    }
  ): Promise<CandlestickData[]> => {
    const interval = options?.intervalOverride ?? TIMEFRAME_DEFAULT_INTERVAL[timeframe]
    const period = TIMEFRAME_PERIOD[timeframe]

    interface HistoryResponse {
      symbol: string
      interval: string
      bars: Array<{
        date: string
        open: number
        high: number
        low: number
        close: number
        volume: number
      }>
      market: string
      source: string
    }

    // When start/end are provided, use date-range mode (don't send period).
    // Otherwise fall back to period mode with period clamped to interval's max lookback.
    const params: Record<string, string> = { symbol, interval }
    if (options?.start && options?.end) {
      params.start = options.start
      params.end = options.end
    } else {
      params.period = clampPeriod(period, interval)
    }

    const response = await apiClient.get<HistoryResponse>('/stocks/history', { params })

    // Transform backend response to frontend CandlestickData format
    // Backend returns { bars: [...] } with 'date' field, frontend expects array with 'time' field
    const bars = response.data.bars || []
    const isIntraday = INTRADAY_INTERVALS.has(interval)
    return bars.map((bar): CandlestickData => ({
      // lightweight-charts needs YYYY-MM-DD for daily+ data, Unix timestamp for intraday
      // For intraday: convert to market-local time since lightweight-charts displays UTC
      time: isIntraday
        ? toMarketLocalTimestamp(bar.date)
        : (bar.date.split('T')[0] ?? bar.date),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
      volume: bar.volume,
    }))
  },

  getIndicators: async (
    symbol: string,
    timeframe: ChartTimeframe = '1M',
    types: string[] = ['sma'],
    options?: {
      maPeriods?: number[]
      rsiPeriod?: number
      bbPeriod?: number
      bbStd?: number
      intervalOverride?: string
      start?: string
      end?: string
    }
  ): Promise<TechnicalIndicatorsData> => {
    const interval = options?.intervalOverride ?? TIMEFRAME_DEFAULT_INTERVAL[timeframe]
    const period = TIMEFRAME_PERIOD[timeframe]

    // Build params: date-range mode when start/end are provided, period mode otherwise.
    // Period is clamped to fit within the interval's max lookback window.
    const params: Record<string, string | number> = {
      symbol,
      types: types.join(','),
      interval,
    }
    if (options?.start && options?.end) {
      params.start = options.start
      params.end = options.end
    } else {
      params.period = clampPeriod(period, interval)
    }
    if (options?.maPeriods != null) params.ma_periods = options.maPeriods.join(',')
    if (options?.rsiPeriod != null) params.rsi_period = options.rsiPeriod
    if (options?.bbPeriod != null) params.bb_period = options.bbPeriod
    if (options?.bbStd != null) params.bb_std = options.bbStd

    const response = await apiClient.get<TechnicalIndicatorsData>('/stocks/indicators', { params })

    // For intraday intervals, indicator time values come as "YYYY-MM-DD HH:MM:SS"
    // and need to be converted to Unix timestamps for lightweight-charts
    const isIntraday = INTRADAY_INTERVALS.has(interval)
    if (isIntraday) {
      convertIndicatorTimesToTimestamps(response.data)
    }

    return response.data
  },

  search: async (query: string, signal?: AbortSignal): Promise<SearchResult[]> => {
    const response = await apiClient.get<{ results: SearchResult[]; count: number }>('/stocks/search', {
      params: { q: query },
      ...(signal && { signal }),
    })
    return response.data.results
  },
}

// Watchlist API
export const watchlistApi = {
  getAll: async (): Promise<Watchlist[]> => {
    const response = await apiClient.get<{ watchlists: Watchlist[]; total: number }>('/watchlists')
    return response.data.watchlists
  },

  get: async (id: number | string): Promise<Watchlist> => {
    interface WatchlistDetailResponse {
      id: number
      userId: number
      name: string
      description?: string | null
      isDefault?: boolean
      items: Array<{ symbol: string }>
      createdAt: string
      updatedAt: string
    }

    const response = await apiClient.get<WatchlistDetailResponse>(`/watchlists/${id}`)
    const data = response.data

    // Map items to symbols for backward compatibility
    return {
      ...data,
      symbols: data.items?.map((item) => item.symbol) || [],
    }
  },

  create: async (name: string, symbols: string[] = []): Promise<Watchlist> => {
    const response = await apiClient.post<Watchlist>('/watchlists', { name, symbols })
    return response.data
  },

  update: async (id: number | string, data: Partial<Watchlist>): Promise<Watchlist> => {
    const response = await apiClient.put<Watchlist>(`/watchlists/${id}`, data)
    return response.data
  },

  delete: async (id: number | string): Promise<void> => {
    await apiClient.delete(`/watchlists/${id}`)
  },

  addSymbol: async (id: number | string, symbol: string): Promise<Watchlist> => {
    const response = await apiClient.post<Watchlist>(`/watchlists/${id}/items`, { symbol })
    return response.data
  },

  removeSymbol: async (id: number | string, symbol: string): Promise<Watchlist> => {
    const response = await apiClient.delete<Watchlist>(`/watchlists/${id}/items/${symbol}`)
    return response.data
  },
}

// Portfolio API
export const portfolioApi = {
  getAll: async (): Promise<Portfolio[]> => {
    const response = await apiClient.get<{ portfolios: Portfolio[]; total: number }>('/portfolios')
    return response.data.portfolios
  },

  get: async (id: string): Promise<Portfolio> => {
    const response = await apiClient.get<Portfolio>(`/portfolios/${id}`)
    return response.data
  },

  create: async (name: string, description?: string): Promise<Portfolio> => {
    const response = await apiClient.post<Portfolio>('/portfolios', { name, description })
    return response.data
  },

  update: async (id: string, data: Partial<Portfolio>): Promise<Portfolio> => {
    const response = await apiClient.put<Portfolio>(`/portfolios/${id}`, data)
    return response.data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/portfolios/${id}`)
  },

  getTransactions: async (
    portfolioId: string,
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<Transaction>> => {
    const response = await apiClient.get<PaginatedResponse<Transaction>>(
      `/portfolios/${portfolioId}/transactions`,
      { params: { page, page_size: pageSize } }
    )
    return response.data
  },

  addTransaction: async (
    portfolioId: string,
    transaction: Omit<Transaction, 'id' | 'portfolioId' | 'createdAt'>
  ): Promise<Transaction> => {
    const response = await apiClient.post<Transaction>(
      `/portfolios/${portfolioId}/transactions`,
      transaction
    )
    return response.data
  },
}

// Alerts API
export const alertsApi = {
  getAll: async (): Promise<Alert[]> => {
    const response = await apiClient.get<{ alerts: Alert[]; total: number }>('/alerts')
    return response.data.alerts
  },

  get: async (id: string): Promise<Alert> => {
    const response = await apiClient.get<Alert>(`/alerts/${id}`)
    return response.data
  },

  create: async (alert: CreateAlertInput): Promise<Alert> => {
    // Convert camelCase to snake_case for backend compatibility
    const payload = {
      symbol: alert.symbol,
      condition_type: alert.conditionType,
      threshold: alert.threshold,
    }
    const response = await apiClient.post<Alert>('/alerts', payload)
    return response.data
  },

  update: async (id: string, data: Partial<Alert>): Promise<Alert> => {
    // Convert camelCase to snake_case for backend compatibility
    const payload: Record<string, unknown> = {}
    if (data.symbol) payload.symbol = data.symbol
    if (data.conditionType) payload.condition_type = data.conditionType
    if (data.threshold) payload.threshold = data.threshold
    if (data.status) payload.status = data.status

    const response = await apiClient.put<Alert>(`/alerts/${id}`, payload)
    return response.data
  },

  delete: async (id: string): Promise<void> => {
    await apiClient.delete(`/alerts/${id}`)
  },

  toggleStatus: async (id: string): Promise<Alert> => {
    const response = await apiClient.post<Alert>(`/alerts/${id}/toggle`)
    return response.data
  },
}

// Reports API
export const reportsApi = {
  getSchedules: async (): Promise<ReportSchedule[]> => {
    const response = await apiClient.get<ReportSchedule[]>('/reports/schedules')
    return response.data
  },

  createSchedule: async (
    schedule: Omit<ReportSchedule, 'id' | 'userId' | 'createdAt' | 'updatedAt'>
  ): Promise<ReportSchedule> => {
    const response = await apiClient.post<ReportSchedule>('/reports/schedules', schedule)
    return response.data
  },

  updateSchedule: async (id: string, data: Partial<ReportSchedule>): Promise<ReportSchedule> => {
    const response = await apiClient.put<ReportSchedule>(`/reports/schedules/${id}`, data)
    return response.data
  },

  deleteSchedule: async (id: string): Promise<void> => {
    await apiClient.delete(`/reports/schedules/${id}`)
  },

  getReports: async (
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<Report>> => {
    const response = await apiClient.get<PaginatedResponse<Report>>('/reports', {
      params: { page, page_size: pageSize },
    })
    return response.data
  },

  getReport: async (id: string): Promise<Report> => {
    const response = await apiClient.get<Report>(`/reports/${id}`)
    return response.data
  },

  generateReport: async (symbols: string[]): Promise<Report> => {
    const response = await apiClient.post<Report>('/reports/generate', { symbols })
    return response.data
  },
}

// News API
export const newsApi = {
  getBySymbol: async (
    symbol: string,
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<NewsArticle>> => {
    const response = await apiClient.get<NewsArticle[]>(`/news/${symbol}`, {
      params: { page, page_size: pageSize },
    })
    // Backend returns direct array, wrap it in paginated format
    const items = response.data
    return {
      items,
      total: items.length,
      page: 1,
      pageSize: items.length,
      totalPages: 1,
    }
  },

  getFeed: async (
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<NewsArticle>> => {
    interface FeedResponse {
      news: NewsArticle[]
      total: number
      page: number
      pageSize: number
      hasMore: boolean
    }
    const response = await apiClient.get<FeedResponse>('/news/feed', {
      params: { page, page_size: pageSize },
    })
    // Transform backend response to frontend PaginatedResponse format
    const { news, total, page: currentPage, pageSize: size, hasMore } = response.data
    return {
      items: news,
      total,
      page: currentPage,
      pageSize: size,
      totalPages: hasMore ? currentPage + 1 : currentPage,
    }
  },

  getMarket: async (
    page: number = 1,
    pageSize: number = 20
  ): Promise<PaginatedResponse<NewsArticle>> => {
    interface MarketResponse {
      news: NewsArticle[]
      total: number
      page: number
      pageSize: number
      hasMore: boolean
    }
    const response = await apiClient.get<MarketResponse>('/news/market', {
      params: { page, page_size: pageSize },
    })
    const { news, total, page: currentPage, pageSize: size, hasMore } = response.data
    return {
      items: news,
      total,
      page: currentPage,
      pageSize: size,
      totalPages: hasMore ? currentPage + 1 : currentPage,
    }
  },

  getTrending: async (): Promise<NewsArticle[]> => {
    interface TrendingResponse {
      news: NewsArticle[]
      market?: string
      fetchedAt: string
    }
    const response = await apiClient.get<TrendingResponse>('/news/trending')
    return response.data.news
  },

  getSentimentTimeline: async (
    symbol: string,
    days = 30,
  ): Promise<SentimentTimelineResponse> => {
    const response = await apiClient.get<SentimentTimelineResponse>(
      `/news/${encodeURIComponent(symbol)}/sentiment-timeline`,
      { params: { days } },
    )
    return response.data
  },

  analyzeArticle: async (article: NewsArticle, language: string = 'en'): Promise<NewsArticle> => {
    interface AnalysisResponse {
      news_id: string
      sentiment_score: number
      sentiment_label: string
      impact_prediction: string
      key_points: string[]
      summary: string
      analyzed_at: string
    }
    const response = await apiClient.post<AnalysisResponse>('/news/analyze', {
      symbol: article.symbol,
      title: article.title,
      summary: article.summary,
      source: article.source,
      published_at: article.publishedAt,
      language,
    })
    // Map the analysis response to update the article with AI analysis
    const analysis = response.data

    // Translation maps for Chinese display
    const sentimentLabelMap: Record<string, Record<string, string>> = {
      zh: { positive: '正面', negative: '负面', neutral: '中性' },
      en: { positive: 'Positive', negative: 'Negative', neutral: 'Neutral' },
    }
    const directionMap: Record<string, Record<string, string>> = {
      zh: { bullish: '看涨', bearish: '看跌', neutral: '中性' },
      en: { bullish: 'Bullish', bearish: 'Bearish', neutral: 'Neutral' },
    }
    const magnitudeMap: Record<string, Record<string, string>> = {
      zh: { high: '高', medium: '中', low: '低' },
      en: { high: 'High', medium: 'Medium', low: 'Low' },
    }
    const timeframeMap: Record<string, Record<string, string>> = {
      zh: { immediate: '即时', short_term: '短期', long_term: '长期' },
      en: { immediate: 'Immediate', short_term: 'Short-term', long_term: 'Long-term' },
    }
    const confidenceMap: Record<string, Record<string, string>> = {
      zh: { high: '高', medium: '中', low: '低' },
      en: { high: 'High', medium: 'Medium', low: 'Low' },
    }

    const lang = language === 'zh' ? 'zh' : 'en'

    // Translate sentiment label
    const translatedSentiment = sentimentLabelMap[lang]?.[analysis.sentiment_label] ?? analysis.sentiment_label

    // Parse and format impact prediction
    let impactText = ''
    try {
      const impact = typeof analysis.impact_prediction === 'string'
        ? JSON.parse(analysis.impact_prediction)
        : analysis.impact_prediction
      const dir = directionMap[lang]?.[impact.direction] ?? impact.direction
      const mag = magnitudeMap[lang]?.[impact.magnitude] ?? impact.magnitude
      const time = timeframeMap[lang]?.[impact.timeframe] ?? impact.timeframe
      const conf = confidenceMap[lang]?.[impact.confidence] ?? impact.confidence
      if (lang === 'zh') {
        impactText = `方向: ${dir}, 幅度: ${mag}, 时间: ${time}, 置信度: ${conf}`
      } else {
        impactText = `Direction: ${dir}, Magnitude: ${mag}, Timeframe: ${time}, Confidence: ${conf}`
      }
    } catch {
      impactText = String(analysis.impact_prediction)
    }

    // Use language-appropriate labels
    const labels = lang === 'zh'
      ? { sentiment: '情感分析', impact: '影响预测', keyPoints: '关键要点', summary: '总结' }
      : { sentiment: 'Sentiment', impact: 'Impact Prediction', keyPoints: 'Key Points', summary: 'Summary' }

    const aiAnalysis = `**${labels.sentiment}**: ${translatedSentiment} (${(analysis.sentiment_score * 100).toFixed(0)}%)\n\n**${labels.impact}**: ${impactText}\n\n**${labels.keyPoints}**:\n${analysis.key_points.map(p => `• ${p}`).join('\n')}\n\n**${labels.summary}**: ${analysis.summary}`

    // Map sentiment label to valid NewsSentiment type
    const sentimentMap: Record<string, NewsArticle['sentiment']> = {
      positive: 'POSITIVE',
      negative: 'NEGATIVE',
      neutral: 'NEUTRAL',
    }
    const mappedSentiment = sentimentMap[analysis.sentiment_label]
    return {
      ...article,
      aiAnalysis,
      ...(mappedSentiment ? { sentiment: mappedSentiment } : {}),
      sentimentScore: analysis.sentiment_score,
    }
  },
}

// Chat API
export const chatApi = {
  createConversation: async (title?: string, symbol?: string): Promise<ChatConversation> => {
    const response = await apiClient.post<ChatConversation>('/chat/conversations', {
      title,
      symbol,
    })
    return response.data
  },

  listConversations: async (limit = 20, offset = 0): Promise<ChatConversationList> => {
    const response = await apiClient.get<ChatConversationList>('/chat/conversations', {
      params: { limit, offset },
    })
    return response.data
  },

  getConversation: async (conversationId: string): Promise<ChatConversation> => {
    const response = await apiClient.get<ChatConversation>(`/chat/conversations/${conversationId}`)
    return response.data
  },

  updateConversation: async (conversationId: string, updates: { title?: string; isArchived?: boolean }): Promise<ChatConversation> => {
    const response = await apiClient.put<ChatConversation>(`/chat/conversations/${conversationId}`, updates)
    return response.data
  },

  deleteConversation: async (conversationId: string): Promise<void> => {
    await apiClient.delete(`/chat/conversations/${conversationId}`)
  },

  getMessages: async (conversationId: string, limit = 50, offset = 0): Promise<ChatMessage[]> => {
    const response = await apiClient.get<ChatMessage[]>(`/chat/conversations/${conversationId}/messages`, {
      params: { limit, offset },
    })
    return response.data
  },

  /** Stream a chat message response via SSE. Returns AbortController for cancellation. */
  streamMessage: (
    conversationId: string,
    content: string,
    symbol: string | undefined,
    language: string,
    onEvent: (event: ChatStreamEvent) => void,
    onError: (err: unknown) => void,
    onDone: () => void,
  ): AbortController => {
    const controller = new AbortController()
    const token = getAccessToken()

    const run = async () => {
      try {
        const resp = await fetch(`/api/v1/chat/conversations/${conversationId}/messages/stream`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          credentials: 'include',
          body: JSON.stringify({ content, symbol, language }),
          signal: controller.signal,
        })

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`)
        }

        const reader = resp.body?.getReader()
        if (!reader) throw new Error('No response body')

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })

          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed.startsWith('data: ')) {
              try {
                const parsed = JSON.parse(trimmed.slice(6)) as ChatStreamEvent
                onEvent(parsed)
              } catch {
                // skip unparseable lines
              }
            }
          }
        }

        onDone()
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          onError(err)
        }
      }
    }

    run()
    return controller
  },
}

// Analysis API (SSE streaming via fetch for auth header support)
export const analysisApi = {
  /**
   * Stream AI analysis for a stock using fetch (supports auth headers).
   * Returns an AbortController so the caller can cancel.
   * @param symbol - Stock symbol
   * @param language - Language for analysis output ('en' or 'zh')
   * @param onEvent - Callback for SSE events
   * @param onError - Callback for errors
   * @param onDone - Callback when stream ends
   */
  streamAnalysis: (
    symbol: string,
    language: string,
    onEvent: (data: Record<string, unknown>) => void,
    onError: (err: unknown) => void,
    onDone: () => void,
  ): AbortController => {
    const controller = new AbortController()
    const token = getAccessToken()
    // Normalize language: 'zh-CN', 'zh-TW' etc. → 'zh', others → 'en'
    const lang = language.toLowerCase().startsWith('zh') ? 'zh' : 'en'

    const run = async () => {
      try {
        const resp = await fetch(`/api/v1/analysis/${symbol}/stream?language=${lang}`, {
          method: 'GET',
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          credentials: 'include',
          signal: controller.signal,
        })

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`)
        }

        const reader = resp.body?.getReader()
        if (!reader) throw new Error('No response body')

        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })

          // Parse SSE lines from buffer
          const lines = buffer.split('\n')
          buffer = lines.pop() ?? ''

          for (const line of lines) {
            const trimmed = line.trim()
            if (trimmed.startsWith('data: ')) {
              try {
                const parsed = JSON.parse(trimmed.slice(6)) as Record<string, unknown>
                onEvent(parsed)
              } catch {
                // skip unparseable lines
              }
            }
          }
        }

        onDone()
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          onError(err)
        }
      }
    }

    run()
    return controller
  },

  /**
   * Get cached analysis (non-streaming)
   */
  getAnalysis: async (symbol: string): Promise<{ content: string; timestamp: string }> => {
    const response = await apiClient.get<{ content: string; timestamp: string }>(
      `/analysis/${symbol}`
    )
    return response.data
  },
}

export { default as apiClient, getErrorMessage, isNetworkError, isTimeoutError } from './client'
export { adminApi } from './admin'
