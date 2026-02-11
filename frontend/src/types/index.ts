// User types
export type UserRole = 'admin' | 'user'
export type AccountStatus = 'active' | 'pending_approval' | 'suspended'

export interface User {
  id: string
  email: string
  role: UserRole
  isActive: boolean
  accountStatus: AccountStatus
  createdAt: string
  updatedAt: string
}

// Pending approval types
export interface PendingApprovalResponse {
  status: 'pending_approval'
  message: string
  pendingToken: string
  email: string
}

export interface RegisterResponse {
  user: User
  requiresApproval: boolean
}

export interface CheckStatusResponse {
  status: 'pending_approval' | 'active' | 'rejected'
  message: string
  rejectionReason?: string
}

// Admin types
export interface UserAdminItem {
  id: number
  email: string
  role: UserRole
  isActive: boolean
  isLocked: boolean
  failedLoginAttempts: number
  lockedUntil: string | null
  createdAt: string
  updatedAt: string
  canUseCustomApiKey: boolean
}

export interface UserListResponse {
  users: UserAdminItem[]
  total: number
}

export interface UpdateUserRequest {
  role?: UserRole
  isActive?: boolean
  isLocked?: boolean
  canUseCustomApiKey?: boolean
}

export interface SystemSettings {
  openaiApiKeySet: boolean
  openaiBaseUrl: string | null
  openaiModel: string
  openaiMaxTokens: number | null
  openaiTemperature: number | null
  embeddingModel: string
  newsFilterModel: string
  newsRetentionDays: number
  finnhubApiKeySet: boolean
  polygonApiKeySet: boolean
  allowUserCustomApiKeys: boolean
  updatedAt: string
  updatedBy: number | null
}

export interface UpdateSystemSettingsRequest {
  openaiApiKey?: string
  openaiBaseUrl?: string
  openaiModel?: string
  openaiMaxTokens?: number
  openaiTemperature?: number
  embeddingModel?: string
  newsFilterModel?: string
  newsRetentionDays?: number
  finnhubApiKey?: string
  polygonApiKey?: string
  allowUserCustomApiKeys?: boolean
}

export interface ApiCallStats {
  chatRequestsToday: number
  analysisRequestsToday: number
  totalTokensToday: number
}

export interface SystemStats {
  totalUsers: number
  totalAdmins: number
  activeUsers: number
  logins24h: number
  apiStats: ApiCallStats
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

export interface NewsRelatedEntity {
  entity: string
  type: 'stock' | 'index' | 'macro'
  score: number
}

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
  sentimentTag?: 'bullish' | 'bearish' | 'neutral'
  aiAnalysis?: string
  relatedEntities?: NewsRelatedEntity[]
  industryTags?: string[]
  eventTags?: string[]
  createdAt: string
}

// Sentiment Timeline types
export interface SentimentTimelineItem {
  date: string
  bullish: number
  bearish: number
  neutral: number
  total: number
  score: number
}

export interface SentimentTimelineResponse {
  symbol: string
  days: number
  data: SentimentTimelineItem[]
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

// Technical Indicator types
export interface IndicatorDataPoint {
  time: string | number
  value: number
}

export interface MAIndicatorData {
  series: IndicatorDataPoint[]
  metadata: Record<string, unknown>
}

export interface MACDIndicatorData {
  macdLine: IndicatorDataPoint[]
  signalLine: IndicatorDataPoint[]
  histogram: IndicatorDataPoint[]
  metadata: Record<string, unknown>
}

export interface BollingerBandsData {
  upper: IndicatorDataPoint[]
  middle: IndicatorDataPoint[]
  lower: IndicatorDataPoint[]
  metadata: Record<string, unknown>
}

export interface TechnicalIndicatorsData {
  symbol: string
  interval: string
  ma?: Record<string, MAIndicatorData>
  rsi?: MAIndicatorData
  macd?: MACDIndicatorData
  bb?: BollingerBandsData
  warnings: string[]
}

// Search types
export type MatchField = 'symbol' | 'name' | 'name_zh' | 'pinyin' | 'pinyin_initial'

export interface SearchResult {
  symbol: string
  name: string
  market: Market
  exchange: string
  matchField?: MatchField
  nameZh?: string
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

// News content settings types
export type NewsContentSource = 'scraper' | 'polygon'

export interface NewsContentSettings {
  source: NewsContentSource
  polygonApiKey: string | null
  retentionDays: number
}

// Admin User Management types (for paginated list)
export interface AdminUser {
  id: number
  email: string
  role: UserRole
  isActive: boolean
  accountStatus: AccountStatus
  apiPermissions: {
    canUseOwnApiKey: boolean
    dailyApiLimit: number | null
  }
  createdAt: string
  lastLoginAt: string | null
}

export interface AdminUserListResponse {
  users: AdminUser[]
  total: number
  page: number
  pageSize: number
  totalPages: number
}

export interface UserFilters {
  search: string
  role: 'all' | 'user' | 'admin'
  status: 'all' | 'active' | 'inactive' | 'pending'
}

// LLM Provider types
export type LlmProviderType = 'openai' | 'anthropic'

export interface LlmProvider {
  id: string
  name: string
  providerType: LlmProviderType
  apiKeySet: boolean
  baseUrl: string | null
  models: string[]
  isEnabled: boolean
  sortOrder: number
  createdAt: string
  updatedAt: string
}

export interface LlmProviderCreate {
  name: string
  providerType: LlmProviderType
  apiKey?: string | null
  baseUrl?: string | null
  models?: string[]
}

export interface LlmProviderUpdate {
  name?: string
  apiKey?: string | null  // "***" = no change
  baseUrl?: string | null  // "" = clear
  models?: string[]
  isEnabled?: boolean
  sortOrder?: number
}

export interface ModelAssignment {
  providerId: string | null
  model: string
}

export interface ModelAssignmentsConfig {
  chat: ModelAssignment
  analysis: ModelAssignment
  synthesis: ModelAssignment
  embedding: ModelAssignment
  newsFilter: ModelAssignment
}

// Admin System Configuration types
export interface SystemConfig {
  llm: {
    apiKey: string | null
    baseUrl: string
    // LangGraph model settings (merged)
    useLocalModels: boolean  // Whether to use local models
    localLlmBaseUrl: string | null  // OpenAI compatible endpoint (vLLM, Ollama, LMStudio, etc.)
    analysisModel: string  // Analysis layer model
    synthesisModel: string  // Synthesis layer model
    maxClarificationRounds: number  // Max clarification rounds (0-5)
    clarificationConfidenceThreshold: number  // Confidence threshold (0.0-1.0)
    anthropicApiKey: string | null  // Anthropic API Key (masked as "***" if set)
    anthropicBaseUrl: string | null  // Custom Anthropic API URL (for proxy)
  }
  news: {
    defaultSource: NewsContentSource
    retentionDays: number
    embeddingModel: string
    filterModel: string
    autoFetchEnabled: boolean
    finnhubApiKey: string | null  // Finnhub API key for news data
  }
  features: {
    allowUserApiKeys: boolean
    allowUserCustomModels: boolean
    enableNewsAnalysis: boolean
    enableStockAnalysis: boolean
    requireRegistrationApproval: boolean
    useTwoPhaseFilter: boolean
  }
  modelAssignments?: ModelAssignmentsConfig
}

// Admin System Monitor types
export interface SystemMonitorStats {
  users: {
    total: number
    active: number
    newToday: number
    newThisWeek: number
  }
  activity: {
    todayLogins: number
    activeConversations: number
    reportsGenerated: number
    apiCallsToday: number
  }
  system: {
    cpuUsage: number
    memoryUsage: number
    diskUsage: number
    uptime: number
  }
  api: {
    totalRequests: number
    averageLatency: number
    errorRate: number
    rateLimitHits: number
  }
}
