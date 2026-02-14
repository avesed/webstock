import apiClient from './client'
import type {
  UserAdminItem,
  UserListResponse,
  UpdateUserRequest,
  SystemSettings,
  UpdateSystemSettingsRequest,
  SystemStats,
  AdminUser,
  AdminUserListResponse,
  UserFilters,
  SystemConfig,
  SystemMonitorStats,
  UserRole,
  LlmProvider,
  LlmProviderCreate,
  LlmProviderUpdate,
  ModelAssignmentsConfig,
  Phase2Config,
  RssFeed,
  RssFeedCreate,
  RssFeedUpdate,
  RssFeedTestResult,
  RssFeedStats,
} from '@/types'

// Backend API format (has separate langgraph section + modelAssignments)
interface BackendSystemConfig {
  llm: {
    apiKey: string | null
    baseUrl: string
    model: string
    maxTokens: number | null
    temperature: number | null
    anthropicApiKey: string | null
    anthropicBaseUrl: string | null
  }
  news: SystemConfig['news']
  features: SystemConfig['features']
  langgraph: {
    localLlmBaseUrl: string | null
    analysisModel: string
    synthesisModel: string
    useLocalModels: boolean
    maxClarificationRounds: number
    clarificationConfidenceThreshold: number
  }
  modelAssignments?: ModelAssignmentsConfig | null
  phase2?: Phase2Config | null
}

// Transform backend format to frontend format (merge langgraph into llm)
function transformConfigFromBackend(backend: BackendSystemConfig): SystemConfig {
  return {
    llm: {
      apiKey: backend.llm.apiKey,
      baseUrl: backend.llm.baseUrl,
      // Merge langgraph settings into llm
      useLocalModels: backend.langgraph.useLocalModels,
      localLlmBaseUrl: backend.langgraph.localLlmBaseUrl,
      analysisModel: backend.langgraph.analysisModel,
      synthesisModel: backend.langgraph.synthesisModel,
      maxClarificationRounds: backend.langgraph.maxClarificationRounds,
      clarificationConfidenceThreshold: backend.langgraph.clarificationConfidenceThreshold,
      anthropicApiKey: backend.llm.anthropicApiKey,
      anthropicBaseUrl: backend.llm.anthropicBaseUrl,
    },
    news: backend.news,
    features: backend.features,
    ...(backend.modelAssignments ? { modelAssignments: backend.modelAssignments } : {}),
    ...(backend.phase2 ? { phase2: backend.phase2 } : {}),
  }
}

// Transform frontend format to backend format (split llm back to llm + langgraph)
function transformConfigToBackend(frontend: SystemConfig): BackendSystemConfig {
  return {
    llm: {
      apiKey: frontend.llm.apiKey,
      baseUrl: frontend.llm.baseUrl,
      model: frontend.modelAssignments?.chat?.model ?? frontend.llm.analysisModel,
      maxTokens: null,
      temperature: null,
      anthropicApiKey: frontend.llm.anthropicApiKey,
      anthropicBaseUrl: frontend.llm.anthropicBaseUrl,
    },
    news: frontend.news,
    features: frontend.features,
    langgraph: {
      localLlmBaseUrl: frontend.llm.localLlmBaseUrl,
      analysisModel: frontend.llm.analysisModel,
      synthesisModel: frontend.llm.synthesisModel,
      useLocalModels: frontend.llm.useLocalModels,
      maxClarificationRounds: frontend.llm.maxClarificationRounds,
      clarificationConfidenceThreshold: frontend.llm.clarificationConfidenceThreshold,
    },
    modelAssignments: frontend.modelAssignments ?? null,
    phase2: frontend.phase2 ?? null,
  }
}

export const adminApi = {
  // User management (simple list)
  listUsers: async (params?: {
    limit?: number
    offset?: number
    role?: string
    isActive?: boolean
    search?: string
  }): Promise<UserListResponse> => {
    const response = await apiClient.get<UserListResponse>('/admin/users', { params })
    return response.data
  },

  // User management (paginated list with filters)
  getUsers: async (page: number, pageSize: number, filters: UserFilters): Promise<AdminUserListResponse> => {
    const params: Record<string, string | number> = {
      page,
      page_size: pageSize,
    }
    if (filters.search) params.search = filters.search
    if (filters.role !== 'all') params.role = filters.role
    if (filters.status !== 'all') params.is_active = filters.status === 'active' ? 'true' : 'false'

    const response = await apiClient.get<AdminUserListResponse>('/admin/users', { params })
    return response.data
  },

  getUser: async (userId: number): Promise<UserAdminItem> => {
    const response = await apiClient.get<UserAdminItem>(`/admin/users/${userId}`)
    return response.data
  },

  updateUser: async (userId: number, data: UpdateUserRequest): Promise<UserAdminItem> => {
    const response = await apiClient.put<UserAdminItem>(`/admin/users/${userId}`, data)
    return response.data
  },

  updateUserStatus: async (userId: number, isActive: boolean): Promise<AdminUser> => {
    const response = await apiClient.put<AdminUser>(`/admin/users/${userId}/status`, { is_active: isActive })
    return response.data
  },

  updateUserRole: async (userId: number, role: UserRole): Promise<AdminUser> => {
    const response = await apiClient.put<AdminUser>(`/admin/users/${userId}/role`, { role })
    return response.data
  },

  resetPassword: async (userId: number, newPassword: string): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>(
      `/admin/users/${userId}/reset-password`,
      { newPassword }
    )
    return response.data
  },

  resetUserPassword: async (userId: number): Promise<{ temporaryPassword: string }> => {
    const response = await apiClient.post<{ temporaryPassword: string }>(`/admin/users/${userId}/reset-password`)
    return response.data
  },

  updateApiPermissions: async (
    userId: number,
    permissions: { canUseOwnApiKey: boolean; dailyApiLimit: number | null }
  ): Promise<AdminUser> => {
    const response = await apiClient.put<AdminUser>(`/admin/users/${userId}/api-permissions`, {
      can_use_own_api_key: permissions.canUseOwnApiKey,
      daily_api_limit: permissions.dailyApiLimit,
    })
    return response.data
  },

  // System settings (simple)
  getSettings: async (): Promise<SystemSettings> => {
    const response = await apiClient.get<SystemSettings>('/admin/settings')
    return response.data
  },

  updateSettings: async (data: UpdateSystemSettingsRequest): Promise<SystemSettings> => {
    const response = await apiClient.put<SystemSettings>('/admin/settings', data)
    return response.data
  },

  // System configuration (detailed)
  // Note: Backend uses separate langgraph section, frontend merges it into llm
  getSystemConfig: async (): Promise<SystemConfig> => {
    const response = await apiClient.get<BackendSystemConfig>('/admin/system/config')
    return transformConfigFromBackend(response.data)
  },

  updateSystemConfig: async (config: Partial<SystemConfig> | SystemConfig): Promise<SystemConfig> => {
    // Handle partial updates (e.g., only features section)
    // Only transform if llm section is provided (full update from SystemSettings)
    let backendPayload: unknown

    if ('llm' in config && config.llm) {
      // Full config update - transform llm to llm + langgraph
      backendPayload = transformConfigToBackend(config as SystemConfig)
    } else {
      // Partial update (e.g., just features or news) - pass through as-is
      backendPayload = config
    }

    const response = await apiClient.put<BackendSystemConfig>('/admin/system/config', backendPayload)
    return transformConfigFromBackend(response.data)
  },

  // System statistics (simple)
  getStats: async (): Promise<SystemStats> => {
    const response = await apiClient.get<SystemStats>('/admin/stats')
    return response.data
  },

  // System statistics (detailed monitor)
  getSystemStats: async (): Promise<SystemMonitorStats> => {
    const response = await apiClient.get<SystemMonitorStats>('/admin/system/stats')
    return response.data
  },

  // User approval management
  approveUser: async (userId: number, sendNotification = true): Promise<AdminUser> => {
    const response = await apiClient.post<AdminUser>(`/admin/users/${userId}/approve`, {
      send_notification: sendNotification,
    })
    return response.data
  },

  rejectUser: async (
    userId: number,
    reason?: string,
    deleteAccount = false
  ): Promise<{ message: string }> => {
    const response = await apiClient.post<{ message: string }>(`/admin/users/${userId}/reject`, {
      reason,
      delete_account: deleteAccount,
    })
    return response.data
  },

  // Create a new user (admin only)
  createUser: async (email: string, password: string, role: UserRole): Promise<AdminUser> => {
    const response = await apiClient.post<AdminUser>('/admin/users', {
      email,
      password,
      role,
    })
    return response.data
  },

  // LLM Provider CRUD
  listLlmProviders: async (): Promise<LlmProvider[]> => {
    const response = await apiClient.get<{ providers: LlmProvider[] }>('/admin/llm-providers')
    return response.data.providers
  },

  createLlmProvider: async (data: LlmProviderCreate): Promise<LlmProvider> => {
    const response = await apiClient.post<LlmProvider>('/admin/llm-providers', data)
    return response.data
  },

  updateLlmProvider: async (id: string, data: LlmProviderUpdate): Promise<LlmProvider> => {
    const response = await apiClient.put<LlmProvider>(`/admin/llm-providers/${id}`, data)
    return response.data
  },

  deleteLlmProvider: async (id: string): Promise<{ message: string }> => {
    const response = await apiClient.delete<{ message: string }>(`/admin/llm-providers/${id}`)
    return response.data
  },

  // News filter statistics
  getFilterStats: async (days = 7): Promise<FilterStats> => {
    const response = await apiClient.get<FilterStats>('/admin/news/filter-stats', {
      params: { days },
    })
    return response.data
  },

  getDailyFilterStats: async (days = 7): Promise<DailyFilterStats> => {
    const response = await apiClient.get<DailyFilterStats>('/admin/news/filter-stats/daily', {
      params: { days },
    })
    return response.data
  },

  triggerNewsMonitor: async (): Promise<{ message: string; taskId: string }> => {
    const response = await apiClient.post<{ message: string; taskId: string }>('/admin/news/trigger-monitor')
    return response.data
  },

  getMonitorStatus: async (): Promise<MonitorStatus> => {
    const response = await apiClient.get<MonitorStatus>('/admin/news/monitor-status')
    return response.data
  },

  // Pipeline tracing
  getArticleTimeline: async (newsId: string): Promise<ArticleTimeline> => {
    const response = await apiClient.get<ArticleTimeline>(`/admin/pipeline/article/${newsId}`)
    return response.data
  },

  getPipelineStats: async (days = 7): Promise<PipelineStats> => {
    const response = await apiClient.get<PipelineStats>('/admin/pipeline/stats', {
      params: { days },
    })
    return response.data
  },

  searchPipelineEvents: async (params: {
    layer?: string
    node?: string
    status?: string
    days?: number
    limit?: number
    offset?: number
  }): Promise<PipelineEventSearchResult> => {
    const response = await apiClient.get<PipelineEventSearchResult>('/admin/pipeline/events', {
      params,
    })
    return response.data
  },

  // Layer 1.5 content fetch & cleaning stats
  getLayer15Stats: async (days = 7): Promise<Layer15Stats> => {
    const response = await apiClient.get<Layer15Stats>('/admin/news/layer15-stats', {
      params: { days },
    })
    return response.data
  },

  // News pipeline multi-agent analysis stats
  getNewsPipelineStats: async (days = 7): Promise<NewsPipelineStats> => {
    const response = await apiClient.get<NewsPipelineStats>('/admin/news/news-pipeline-stats', {
      params: { days },
    })
    return response.data
  },

  // Source quality stats
  getSourceStats: async (days = 7): Promise<SourceStats> => {
    const response = await apiClient.get<SourceStats>('/admin/news/source-stats', {
      params: { days },
    })
    return response.data
  },

  // RSS Feed Management
  listRssFeeds: async (params?: { category?: string; isEnabled?: boolean }): Promise<{ feeds: RssFeed[]; total: number }> => {
    const response = await apiClient.get<{ feeds: RssFeed[]; total: number }>('/admin/rss-feeds', { params })
    return response.data
  },

  createRssFeed: async (data: RssFeedCreate): Promise<RssFeed> => {
    const response = await apiClient.post<RssFeed>('/admin/rss-feeds', data)
    return response.data
  },

  updateRssFeed: async (id: string, data: RssFeedUpdate): Promise<RssFeed> => {
    const response = await apiClient.put<RssFeed>(`/admin/rss-feeds/${id}`, data)
    return response.data
  },

  deleteRssFeed: async (id: string): Promise<{ message: string }> => {
    const response = await apiClient.delete<{ message: string }>(`/admin/rss-feeds/${id}`)
    return response.data
  },

  toggleRssFeed: async (id: string): Promise<RssFeed> => {
    const response = await apiClient.post<RssFeed>(`/admin/rss-feeds/${id}/toggle`)
    return response.data
  },

  testRssFeed: async (rsshubRoute: string, fulltextMode: boolean = false): Promise<RssFeedTestResult> => {
    const response = await apiClient.post<RssFeedTestResult>('/admin/rss-feeds/test', { rsshubRoute, fulltextMode })
    return response.data
  },

  getRssFeedStats: async (days: number = 7): Promise<RssFeedStats> => {
    const response = await apiClient.get<RssFeedStats>('/admin/rss-feeds/stats', { params: { days } })
    return response.data
  },

  triggerRssMonitor: async (): Promise<{ message: string; taskId?: string }> => {
    const response = await apiClient.post<{ message: string; taskId?: string }>('/admin/rss-feeds/trigger')
    return response.data
  },
}

// Filter stats types
export interface FilterStats {
  periodDays: number
  counts: {
    initialFilter: {
      useful: number
      uncertain: number
      skip: number
      total: number
    }
    deepFilter: {
      keep: number
      delete: number
      total: number
    }
    errors: {
      filterError: number
      embeddingError: number
    }
    embedding: {
      success: number
      error: number
    }
    layer1Scoring?: {
      discard: number
      lightweight: number
      fullAnalysis: number
      criticalEvent: number
      total: number
    }
  }
  rates: {
    initialSkipRate: number
    initialPassRate: number
    deepKeepRate: number
    deepDeleteRate: number
    filterErrorRate: number
    embeddingErrorRate: number
    layer1DiscardRate?: number
    layer1PassRate?: number
  }
  tokens: {
    initialFilter: TokenUsage
    deepFilter: TokenUsage
    total: TokenUsage
    days: number
    layer1Macro?: TokenUsage
    layer1Market?: TokenUsage
    layer1Signal?: TokenUsage
  }
  alerts: FilterAlert[]
}

export interface TokenUsage {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  estimatedCostUsd: number
}

export interface FilterAlert {
  stat: string
  rate: string
  level: 'warning' | 'critical'
  message: string
}

export interface DailyFilterStats {
  days: number
  data: DailyFilterStatsItem[]
}

export interface MonitorStatus {
  status: 'running' | 'idle'
  progress: {
    stage: string
    message: string
    percent: number
    updatedAt: string
  } | null
  lastRun: {
    finishedAt: string
    stats: Record<string, number>
  } | null
  nextRunAt: string | null
}

export interface DailyFilterStatsItem {
  date: string
  initialUseful: number
  initialUncertain: number
  initialSkip: number
  fineKeep: number
  fineDelete: number
  filterError: number
  embeddingSuccess: number
  embeddingError: number
  initialInputTokens: number
  initialOutputTokens: number
  deepInputTokens: number
  deepOutputTokens: number
}

// Pipeline tracing types
export interface PipelineEvent {
  id: string
  newsId: string
  layer: string
  node: string
  status: string
  durationMs: number | null
  metadata: Record<string, unknown> | null
  error: string | null
  createdAt: string
}

export interface ArticleTimeline {
  newsId: string
  title: string | null
  symbol: string | null
  events: PipelineEvent[]
  totalDurationMs: number | null
}

export interface NodeStats {
  layer: string
  node: string
  count: number
  successCount: number
  errorCount: number
  avgMs: number | null
  p50Ms: number | null
  p95Ms: number | null
  maxMs: number | null
}

export interface PipelineStats {
  periodDays: number
  nodes: NodeStats[]
}

export interface PipelineEventSearchResult {
  events: PipelineEvent[]
  total: number
}

// Source stats types
export interface SourceStatsItem {
  source: string
  total: number
  initialUseful: number
  initialUncertain: number
  fineKeep: number
  fineDelete: number
  embedded: number
  fetchFailed: number
  avgEntityCount: number | null
  sentimentDistribution: Record<string, number> | null
  keepRate: number | null
  fetchRate: number | null
}

export interface SourceStats {
  periodDays: number
  sources: SourceStatsItem[]
  totalSources: number
}

// Layer 1.5 content fetch & cleaning types
export interface Layer15FetchStats {
  total: number
  success: number
  errors: number
  avgMs: number | null
  p50Ms: number | null
  p95Ms: number | null
  avgImagesFound: number
  avgImagesDownloaded: number
  articlesWithImages: number
}

export interface Layer15ProviderDistribution {
  provider: string
  count: number
}

export interface Layer15CleaningStats {
  total: number
  success: number
  errors: number
  avgMs: number | null
  p50Ms: number | null
  p95Ms: number | null
  avgRetentionRate: number | null
  articlesWithVisualData: number
  avgImageCount: number
  avgInsightsLength: number
}

export interface Layer15Stats {
  periodDays: number
  fetch: Layer15FetchStats
  providerDistribution: Layer15ProviderDistribution[]
  cleaning: Layer15CleaningStats
}

// News pipeline multi-agent analysis types
export interface NewsPipelineRoutingStats {
  total: number
  fullAnalysis: number
  lightweight: number
  criticalEvents: number
  scoringErrors: number
}

export interface NewsPipelineTokenStage {
  inputTokens: number
  outputTokens: number
  totalTokens: number
  estimatedCostUsd: number
}

export interface NewsPipelineTokenStats {
  scoring: NewsPipelineTokenStage
  multiAgent: NewsPipelineTokenStage
  lightweight: NewsPipelineTokenStage
  total: NewsPipelineTokenStage
}

export interface ScoreDistributionBucket {
  bucket: string
  count: number
  fullAnalysis: number
  lightweight: number
  critical: number
}

export interface NewsPipelineCacheStats {
  total: number
  avgCacheHitRate: number | null
  cacheHits: number
  totalCachedTokens: number
  totalPromptTokens: number
}

export interface NewsPipelineNodeLatency {
  node: string
  count: number
  success: number
  errors: number
  avgMs: number | null
  p50Ms: number | null
  p95Ms: number | null
}

export interface NewsPipelineStats {
  periodDays: number
  routing: NewsPipelineRoutingStats
  tokens: NewsPipelineTokenStats
  scoreDistribution: ScoreDistributionBucket[]
  cacheStats: NewsPipelineCacheStats
  nodeLatency: NewsPipelineNodeLatency[]
}
