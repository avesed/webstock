// User types
export interface User {
  id: string
  email: string
  isActive: boolean
  createdAt: string
  updatedAt: string
}

export interface AuthTokens {
  accessToken: string
  tokenType: string
  expiresIn: number
}

export interface LoginCredentials {
  email: string
  password: string
}

export interface RegisterCredentials {
  email: string
  password: string
  confirmPassword: string
}

// Stock types (SH = Shanghai, SZ = Shenzhen A-shares)
export type Market = 'US' | 'HK' | 'CN' | 'SH' | 'SZ' | 'METAL'

// Precious metals types
export type WeightUnit = 'troy_oz' | 'gram' | 'kilogram'
export type Currency = 'USD' | 'EUR' | 'CNY' | 'GBP' | 'JPY' | 'HKD'

export interface StockQuote {
  symbol: string
  name: string
  market: Market
  price: number
  change: number
  changePercent: number
  open: number
  dayHigh: number
  dayLow: number
  previousClose: number
  volume: number
  marketCap?: number
  timestamp: string
}

export interface StockInfo {
  symbol: string
  name: string
  market: Market
  sector?: string
  industry?: string
  description?: string
  website?: string
  employees?: number
  headquarters?: string
  ceo?: string
  founded?: string
}

export interface StockFinancials {
  symbol: string
  revenue?: number
  revenueGrowth?: number
  netIncome?: number
  eps?: number
  epsGrowth?: number
  peRatio?: number
  pbRatio?: number
  debtToEquity?: number
  currentRatio?: number
  roe?: number
  roa?: number
  grossMargin?: number
  operatingMargin?: number
  netMargin?: number
  dividendYield?: number
  payoutRatio?: number
}

export interface CandlestickData {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export type ChartTimeframe = '1D' | '1H' | '1W' | '1M' | '3M' | '6M' | '1Y' | '5Y' | 'ALL'

// Watchlist types
export interface Watchlist {
  id: number
  userId: number
  name: string
  description?: string | null
  isDefault?: boolean
  symbols?: string[]  // For backward compatibility, mapped from items
  items?: WatchlistItem[]  // Raw items from API
  itemCount?: number  // Available in list response
  createdAt: string
  updatedAt: string
}

export interface WatchlistItemDetail {
  id: number
  watchlistId: number
  symbol: string
  notes?: string | null
  alertPriceAbove?: number | null
  alertPriceBelow?: number | null
  addedAt: string
}

export interface WatchlistItem {
  symbol: string
  quote?: StockQuote
}

// Portfolio types
export interface Portfolio {
  id: string
  userId: string
  name: string
  description?: string
  holdings: Holding[]
  totalValue: number
  totalCost: number
  totalGain: number
  totalGainPercent: number
  createdAt: string
  updatedAt: string
}

export interface Holding {
  id: string
  portfolioId: string
  symbol: string
  quantity: number
  averageCost: number
  currentPrice?: number
  currentValue?: number
  gain?: number
  gainPercent?: number
}

export interface Transaction {
  id: string
  portfolioId: string
  symbol: string
  type: 'BUY' | 'SELL'
  quantity: number
  price: number
  fee: number
  date: string
  notes?: string
  createdAt: string
}

// Alert types
export type AlertCondition = 'ABOVE' | 'BELOW' | 'PERCENT_CHANGE_UP' | 'PERCENT_CHANGE_DOWN'
export type AlertStatus = 'ACTIVE' | 'TRIGGERED' | 'DISABLED'

export interface Alert {
  id: string
  userId: string
  symbol: string
  conditionType: AlertCondition
  threshold: number
  status: AlertStatus
  triggeredAt?: string
  createdAt: string
  updatedAt: string
}

export interface CreateAlertInput {
  symbol: string
  conditionType: AlertCondition
  threshold: number
}

// Report types
export type ReportFrequency = 'DAILY' | 'WEEKLY' | 'MONTHLY'
export type ReportStatus = 'PENDING' | 'GENERATING' | 'COMPLETED' | 'FAILED'

export interface ReportSchedule {
  id: string
  userId: string
  name: string
  frequency: ReportFrequency
  dayOfWeek?: number // 0-6 for weekly
  dayOfMonth?: number // 1-31 for monthly
  time: string // HH:MM format
  symbols: string[]
  isActive: boolean
  createdAt: string
  updatedAt: string
}

export interface Report {
  id: string
  scheduleId?: string
  userId: string
  title: string
  content: string
  symbols: string[]
  status: ReportStatus
  generatedAt?: string
  createdAt: string
}

// News types
export type NewsSentiment = 'POSITIVE' | 'NEGATIVE' | 'NEUTRAL'

export interface NewsArticle {
  id: string
  symbol?: string
  title: string
  summary?: string
  source: string
  url: string
  imageUrl?: string
  publishedAt: string
  sentiment?: NewsSentiment
  sentimentScore?: number
  aiAnalysis?: string
  createdAt: string
}

// AI Analysis types
export type AnalysisType = 'FUNDAMENTAL' | 'TECHNICAL' | 'SENTIMENT' | 'COMPREHENSIVE'

export interface AnalysisRequest {
  symbol: string
  analysisTypes: AnalysisType[]
  language?: 'en' | 'zh'
}

export interface AnalysisResult {
  symbol: string
  analysisType: AnalysisType
  content: string
  timestamp: string
}

export interface StreamingAnalysisChunk {
  type: 'chunk' | 'complete' | 'error'
  analysisType?: AnalysisType
  content?: string
  error?: string
}

// API Response types
export interface ApiResponse<T> {
  data: T
  message?: string
}

export interface ApiError {
  detail: string
  code?: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  pageSize: number
  totalPages: number
}

// Chart types
export interface ChartOptions {
  timeframe: ChartTimeframe
  showVolume: boolean
  showMA: boolean
  maPeriods: number[]
  showBollinger: boolean
  bollingerPeriod: number
  bollingerStdDev: number
}

// Search types
export interface SearchResult {
  symbol: string
  name: string
  market: Market
  exchange: string
}

// Theme types
export type Theme = 'light' | 'dark' | 'system'

// Chat types
export interface ChatConversation {
  id: string
  title: string | null
  symbol: string | null
  createdAt: string
  updatedAt: string
  isArchived: boolean
  lastMessage: string | null
  messageCount: number
}

export interface ChatMessage {
  id: string
  conversationId: string
  role: 'user' | 'assistant' | 'system'
  content: string
  tokenCount: number | null
  model: string | null
  toolCalls: Record<string, unknown> | null
  ragContext: Record<string, unknown> | null
  createdAt: string
}

export interface ChatConversationList {
  conversations: ChatConversation[]
  total: number
}

export interface ChatStreamEvent {
  type: 'message_start' | 'content_delta' | 'rag_sources' | 'message_end' | 'error' | 'heartbeat' | 'timeout' | 'tool_call_start' | 'tool_call_result'
  content?: string
  conversationId?: string
  messageId?: string
  sources?: Array<Record<string, unknown>>
  tokenCount?: number
  model?: string
  error?: string
  timestamp?: number
  // Tool call fields
  toolCallId?: string
  toolName?: string
  toolArguments?: Record<string, unknown>
  toolLabel?: string
  resultSummary?: string
  success?: boolean
}

export interface ToolCallStatus {
  id: string
  name: string
  label: string
  status: 'running' | 'completed' | 'failed'
}
