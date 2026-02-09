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
} from '@/types'

// Backend API format (has separate langgraph section)
interface BackendSystemConfig {
  llm: {
    apiKey: string | null
    baseUrl: string
    model: string
    maxTokens: number | null
    temperature: number | null
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
    },
    news: backend.news,
    features: backend.features,
  }
}

// Transform frontend format to backend format (split llm back to llm + langgraph)
function transformConfigToBackend(frontend: SystemConfig): BackendSystemConfig {
  return {
    llm: {
      apiKey: frontend.llm.apiKey,
      baseUrl: frontend.llm.baseUrl,
      model: frontend.llm.analysisModel, // Use analysis model as default
      maxTokens: null, // Not used by LangGraph anymore
      temperature: null, // Not used by LangGraph anymore
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
  }
  rates: {
    initialSkipRate: number
    initialPassRate: number
    deepKeepRate: number
    deepDeleteRate: number
    filterErrorRate: number
    embeddingErrorRate: number
  }
  tokens: {
    initialFilter: TokenUsage
    deepFilter: TokenUsage
    total: TokenUsage
    days: number
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
