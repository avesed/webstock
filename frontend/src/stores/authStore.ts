import { create } from 'zustand'
import type { User } from '@/types'
import {
  setAccessToken,
  clearAccessToken,
  logout as authLogout,
  emitAuthEvent,
  onAuthEvent,
} from '@/lib/auth'
import { authApi } from '@/api'
import { getErrorMessage } from '@/api/client'

interface AuthState {
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  error: string | null
}

interface AuthActions {
  login: (email: string, password: string) => Promise<boolean>
  register: (email: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  initAuth: () => Promise<void>
  clearError: () => void
  setUser: (user: User | null) => void
  isAdmin: () => boolean
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

    // Actions
    login: async (email: string, password: string): Promise<boolean> => {
      set({ isLoading: true, error: null })

      try {
        const tokens = await authApi.login({ email, password })
        const accessToken = tokens.accessToken
        setAccessToken(accessToken, tokens.expiresIn)

        // 显式传递 token
        const user = await authApi.me(accessToken)
        set({ user, isAuthenticated: true, isLoading: false })
        emitAuthEvent('login')
        return true
      } catch (error) {
        const message = getErrorMessage(error)
        set({ error: message, isLoading: false })
        return false
      }
    },

    register: async (email: string, password: string): Promise<boolean> => {
      set({ isLoading: true, error: null })

      try {
        await authApi.register({ email, password, confirmPassword: password })
        // Auto-login after registration
        return get().login(email, password)
      } catch (error) {
        const message = getErrorMessage(error)
        set({ error: message, isLoading: false })
        return false
      }
    },

    logout: async (): Promise<void> => {
      set({ isLoading: true })

      try {
        await authLogout()
      } finally {
        clearAccessToken()
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
  }
})
