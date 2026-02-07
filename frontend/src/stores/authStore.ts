import { create } from 'zustand'
import type { User, PendingApprovalResponse, RegisterResponse } from '@/types'
import {
  setAccessToken,
  clearAccessToken,
  logout as authLogout,
  emitAuthEvent,
  onAuthEvent,
} from '@/lib/auth'
import { setPendingSession } from '@/lib/pendingSession'
import { authApi } from '@/api'
import { getErrorMessage } from '@/api/client'
import { useStockStore } from './stockStore'

// Type guard to check if response is pending approval
function isPendingApprovalResponse(
  response: unknown
): response is PendingApprovalResponse {
  return (
    typeof response === 'object' &&
    response !== null &&
    'status' in response &&
    (response as PendingApprovalResponse).status === 'pending_approval'
  )
}

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
  pendingApproval: boolean
  requiresApproval: boolean
}

interface AuthActions {
  login: (email: string, password: string) => Promise<{ success: boolean; pendingApproval?: boolean }>
  register: (email: string, password: string) => Promise<{ success: boolean; requiresApproval?: boolean }>
  logout: () => Promise<void>
  initAuth: () => Promise<void>
  clearError: () => void
  setUser: (user: User | null) => void
  isAdmin: () => boolean
  resetPendingState: () => void
}

type AuthStore = AuthState & AuthActions

export const useAuthStore = create<AuthStore>((set, get) => {
  // Subscribe to auth events
  onAuthEvent((event) => {
    if (event === 'logout' || event === 'token_expired') {
      set({ user: null, isAuthenticated: false })
    }
  })

  return {
    // State
    user: null,
    isAuthenticated: false,
    isLoading: true,
    error: null,
    pendingApproval: false,
    requiresApproval: false,

    // Actions
    login: async (email: string, password: string): Promise<{ success: boolean; pendingApproval?: boolean }> => {
      set({ isLoading: true, error: null, pendingApproval: false })

      try {
        const response = await authApi.login({ email, password })

        // Check if account is pending approval
        if (isPendingApprovalResponse(response)) {
          setPendingSession({
            email: response.email,
            pendingToken: response.pendingToken,
          })
          set({ isLoading: false, pendingApproval: true })
          return { success: false, pendingApproval: true }
        }

        // Normal login flow
        const accessToken = response.accessToken
        setAccessToken(accessToken, response.expiresIn)

        // 显式传递 token
        const user = await authApi.me(accessToken)
        set({ user, isAuthenticated: true, isLoading: false })
        // Load user-specific recent searches
        useStockStore.getState().loadUserRecentSearches(user.id)
        emitAuthEvent('login')
        return { success: true }
      } catch (error) {
        const message = getErrorMessage(error)
        set({ error: message, isLoading: false })
        return { success: false }
      }
    },

    register: async (email: string, password: string): Promise<{ success: boolean; requiresApproval?: boolean }> => {
      set({ isLoading: true, error: null, requiresApproval: false })

      try {
        const response: RegisterResponse = await authApi.register({ email, password, confirmPassword: password })

        // Check if approval is required
        if (response.requiresApproval) {
          set({ isLoading: false, requiresApproval: true })
          return { success: true, requiresApproval: true }
        }

        // Auto-login after registration (only if no approval required)
        const loginResult = await get().login(email, password)
        return { success: loginResult.success }
      } catch (error) {
        const message = getErrorMessage(error)
        set({ error: message, isLoading: false })
        return { success: false }
      }
    },

    logout: async (): Promise<void> => {
      set({ isLoading: true })

      try {
        await authLogout()
      } finally {
        clearAccessToken()
        // Clear user-specific recent searches from memory
        useStockStore.getState().clearUserSession()
        set({ user: null, isAuthenticated: false, isLoading: false })
        emitAuthEvent('logout')
      }
    },

    initAuth: async (): Promise<void> => {
      set({ isLoading: true })

      try {
        // 延迟一点时间确保 cookie 已经可用
        await new Promise(resolve => setTimeout(resolve, 100))

        // 步骤1: 使用 refresh token cookie 获取新的 access token
        const refreshResponse = await fetch('/api/v1/auth/refresh', {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
        })

        if (!refreshResponse.ok) {
          set({ user: null, isAuthenticated: false, isLoading: false })
          return
        }

        const tokens = await refreshResponse.json()
        const accessToken = tokens.accessToken

        if (!accessToken) {
          set({ user: null, isAuthenticated: false, isLoading: false })
          return
        }

        // 保存 token 到内存
        setAccessToken(accessToken, tokens.expiresIn)

        // 步骤2: 使用 access token 获取用户信息
        const meResponse = await fetch('/api/v1/auth/me', {
          method: 'GET',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${accessToken}`,
          },
        })

        if (!meResponse.ok) {
          clearAccessToken()
          set({ user: null, isAuthenticated: false, isLoading: false })
          return
        }

        const user = await meResponse.json()
        set({ user, isAuthenticated: true, isLoading: false })
        // Load user-specific recent searches
        useStockStore.getState().loadUserRecentSearches(user.id)
      } catch (error) {
        clearAccessToken()
        set({ user: null, isAuthenticated: false, isLoading: false })
      }
    },

    clearError: () => {
      set({ error: null })
    },

    setUser: (user: User | null) => {
      set({ user, isAuthenticated: user !== null })
    },

    isAdmin: (): boolean => {
      const { user } = get()
      return user?.role === 'admin'
    },

    resetPendingState: () => {
      set({ pendingApproval: false, requiresApproval: false })
    },
  }
})
