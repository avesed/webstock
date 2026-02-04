import axios, {
  type AxiosInstance,
  type AxiosError,
  type InternalAxiosRequestConfig,
} from 'axios'
import {
  getAccessToken,
  getValidAccessToken,
  clearAccessToken,
  emitAuthEvent,
} from '@/lib/auth'
import type { ApiError } from '@/types'

// Create axios instance with default configuration
const apiClient: AxiosInstance = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true, // Important for HttpOnly cookies
})

// Track if we're currently refreshing the token
let isRefreshing = false
let failedQueue: Array<{
  resolve: (token: string | null) => void
  reject: (error: unknown) => void
}> = []

const processQueue = (error: unknown, token: string | null = null) => {
  failedQueue.forEach((prom) => {
    if (error) {
      prom.reject(error)
    } else {
      prom.resolve(token)
    }
  })
  failedQueue = []
}

// Request interceptor - add auth token
apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig) => {
    // Skip auth for public endpoints
    const publicEndpoints = ['/auth/login', '/auth/register', '/auth/refresh']
    const isPublicEndpoint = publicEndpoints.some((endpoint) =>
      config.url?.includes(endpoint)
    )

    if (!isPublicEndpoint) {
      const token = getAccessToken()
      const authHeader = token ? `Bearer ${token}` : undefined
      console.log('[Auth] request interceptor', {
        url: config.url,
        hasToken: Boolean(token),
        authorization: authHeader ?? '(missing)',
      })
      if (token && authHeader) {
        config.headers.Authorization = authHeader
      }
    }

    return config
  },
  (error: unknown) => {
    return Promise.reject(error)
  }
)

// Response interceptor - handle auth errors and token refresh
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiError>) => {
    const originalRequest = error.config

    // If no config, reject immediately
    if (!originalRequest) {
      return Promise.reject(error)
    }

    // Handle 401 errors
    if (error.response?.status === 401) {
      // Skip refresh for auth endpoints (including /auth/me which handles its own token)
      const authEndpoints = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout', '/auth/me']
      const isAuthEndpoint = authEndpoints.some((endpoint) =>
        originalRequest.url?.includes(endpoint)
      )

      if (isAuthEndpoint) {
        return Promise.reject(error)
      }

      // Check if we've already tried to refresh for this request
      // Using a custom property on the config to track retry status
      const configWithRetry = originalRequest as InternalAxiosRequestConfig & { _retry?: boolean }
      if (configWithRetry._retry) {
        clearAccessToken()
        emitAuthEvent('logout')
        return Promise.reject(error)
      }

      // Mark this request as retried
      configWithRetry._retry = true

      // If already refreshing, queue this request
      if (isRefreshing) {
        return new Promise((resolve, reject) => {
          failedQueue.push({
            resolve: (token: string | null) => {
              if (token) {
                originalRequest.headers.Authorization = `Bearer ${token}`
              }
              resolve(apiClient(originalRequest))
            },
            reject,
          })
        })
      }

      isRefreshing = true

      try {
        const newToken = await getValidAccessToken()

        if (newToken) {
          processQueue(null, newToken)
          originalRequest.headers.Authorization = `Bearer ${newToken}`
          emitAuthEvent('token_refreshed')
          return apiClient(originalRequest)
        } else {
          processQueue(new Error('Failed to refresh token'))
          clearAccessToken()
          emitAuthEvent('logout')
          return Promise.reject(error)
        }
      } catch (refreshError) {
        processQueue(refreshError)
        clearAccessToken()
        emitAuthEvent('logout')
        return Promise.reject(refreshError)
      } finally {
        isRefreshing = false
      }
    }

    // Handle other errors
    return Promise.reject(error)
  }
)

/**
 * Get error message from API error response
 */
export function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiError>
    if (axiosError.response?.data?.detail) {
      return axiosError.response.data.detail
    }
    if (axiosError.message) {
      return axiosError.message
    }
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'An unexpected error occurred'
}

/**
 * Check if error is a network error
 */
export function isNetworkError(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    return !error.response && error.code !== 'ECONNABORTED'
  }
  return false
}

/**
 * Check if error is a timeout error
 */
export function isTimeoutError(error: unknown): boolean {
  if (axios.isAxiosError(error)) {
    return error.code === 'ECONNABORTED'
  }
  return false
}

export default apiClient
