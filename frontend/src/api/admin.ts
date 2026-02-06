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
  getSystemConfig: async (): Promise<SystemConfig> => {
    const response = await apiClient.get<SystemConfig>('/admin/system/config')
    return response.data
  },

  updateSystemConfig: async (config: Partial<SystemConfig>): Promise<SystemConfig> => {
    const response = await apiClient.put<SystemConfig>('/admin/system/config', config)
    return response.data
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
}
