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
  Watchlist,
  Portfolio,
  Transaction,
  Alert,
  CreateAlertInput,
  ReportSchedule,
  Report,
  NewsArticle,
  SearchResult,
  PaginatedResponse,
  ChatConversation,
  ChatMessage,
  ChatConversationList,
  ChatStreamEvent,
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

// Auth API
export const authApi = {
  login: async (credentials: LoginCredentials): Promise<AuthTokens> => {
    const response = await apiClient.post<AuthTokens>('/auth/login', credentials)
    return response.data
  },

  register: async (credentials: RegisterCredentials): Promise<User> => {
    const response = await apiClient.post<User>('/auth/register', credentials)
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
}

// Stock API
export const stockApi = {
  getQuote: async (symbol: string): Promise<StockQuote> => {
    const response = await apiClient.get<StockQuote>(`/stocks/${symbol}/quote`)
    return response.data
  },

  getInfo: async (symbol: string): Promise<StockInfo> => {
    const response = await apiClient.get<StockInfo>(`/stocks/${symbol}/info`)
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

    const response = await apiClient.get<FinancialsResponse>(`/stocks/${symbol}/financials`)
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
    timeframe: ChartTimeframe = '1M'
  ): Promise<CandlestickData[]> => {
    // Map frontend timeframe to backend period and interval
    const timeframeMap: Record<ChartTimeframe, { period: string; interval: string }> = {
      '1H': { period: '1d', interval: '1m' },
      '1D': { period: '1d', interval: '5m' },
      '1W': { period: '5d', interval: '15m' },
      '1M': { period: '1mo', interval: '1d' },
      '3M': { period: '3mo', interval: '1d' },
      '6M': { period: '6mo', interval: '1d' },
      '1Y': { period: '1y', interval: '1d' },
      '5Y': { period: '5y', interval: '1wk' },
      'ALL': { period: 'max', interval: '1mo' },
    }

    const { period, interval } = timeframeMap[timeframe]

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

    const response = await apiClient.get<HistoryResponse>(`/stocks/${symbol}/history`, {
      params: { period, interval },
    })

    // Transform backend response to frontend CandlestickData format
    // Backend returns { bars: [...] } with 'date' field, frontend expects array with 'time' field
    const bars = response.data.bars || []
    const isIntraday = ['1m', '5m', '15m', '1h'].includes(interval)
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

  search: async (query: string): Promise<SearchResult[]> => {
    const response = await apiClient.get<SearchResult[]>('/stocks/search', {
      params: { q: query },
    })
    return response.data
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

  getTrending: async (): Promise<NewsArticle[]> => {
    interface TrendingResponse {
      news: NewsArticle[]
      market?: string
      fetchedAt: string
    }
    const response = await apiClient.get<TrendingResponse>('/news/trending')
    return response.data.news
  },

  analyzeArticle: async (article: NewsArticle): Promise<NewsArticle> => {
    const response = await apiClient.post<NewsArticle>('/news/analyze', {
      symbol: article.symbol,
      title: article.title,
      summary: article.summary,
      source: article.source,
      published_at: article.publishedAt,
    })
    return response.data
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
          body: JSON.stringify({ content, symbol }),
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
   */
  streamAnalysis: (
    symbol: string,
    onEvent: (data: Record<string, unknown>) => void,
    onError: (err: unknown) => void,
    onDone: () => void,
  ): AbortController => {
    const controller = new AbortController()
    const token = getAccessToken()

    const run = async () => {
      try {
        const resp = await fetch(`/api/v1/analysis/${symbol}/stream`, {
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
