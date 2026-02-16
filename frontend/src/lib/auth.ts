/**
 * Authentication Token Management
 *
 * Security approach:
 * - Access token: Stored in memory (not localStorage) for XSS protection
 * - Refresh token: Handled via HttpOnly cookies by the backend
 *
 * The access token is short-lived (15-30 min) and stored only in memory.
 * When the page refreshes, we use the refresh token cookie to get a new access token.
 */

// In-memory storage for access token (not persisted to avoid XSS)
let accessToken: string | null = null
let tokenExpiresAt: number | null = null

// Token refresh promise to prevent multiple simultaneous refresh attempts
let refreshPromise: Promise<string | null> | null = null

/**
 * Store the access token in memory
 */
export function setAccessToken(token: string, expiresIn: number): void {
  accessToken = token
  // Calculate expiration time with a buffer (10% of expiry time, max 60 seconds)
  // This ensures short-lived tokens don't expire immediately
  const bufferSeconds = Math.min(60, Math.max(5, Math.floor(expiresIn * 0.1)))
  const safeExpiresIn = Math.max(60, expiresIn - bufferSeconds) // At least keep 60 seconds
  tokenExpiresAt = Date.now() + safeExpiresIn * 1000
  console.log('[Auth] setAccessToken', {
    hasToken: Boolean(token),
    expiresIn,
    bufferSeconds,
    safeExpiresIn,
    expiresAt: tokenExpiresAt,
  })
}

/**
 * Get the current access token
 */
export function getAccessToken(): string | null {
  // Return null if token is expired
  if (tokenExpiresAt && Date.now() >= tokenExpiresAt) {
    console.log('[Auth] getAccessToken: expired token cleared', {
      expiresAt: tokenExpiresAt,
      now: Date.now(),
    })
    accessToken = null
    tokenExpiresAt = null
    return null
  }
  console.log('[Auth] getAccessToken: returning token', {
    hasToken: Boolean(accessToken),
    expiresAt: tokenExpiresAt,
  })
  return accessToken
}

/**
 * Check if the access token is present and not expired
 */
export function hasValidAccessToken(): boolean {
  if (!accessToken || !tokenExpiresAt) return false
  return Date.now() < tokenExpiresAt
}

/**
 * Clear the access token from memory
 */
export function clearAccessToken(): void {
  accessToken = null
  tokenExpiresAt = null
}

/**
 * Get the time until the token expires (in seconds)
 */
export function getTokenExpiresIn(): number {
  if (!tokenExpiresAt) return 0
  const remaining = Math.max(0, tokenExpiresAt - Date.now())
  return Math.floor(remaining / 1000)
}

/**
 * Check if the token is about to expire (within threshold seconds)
 */
export function isTokenExpiringSoon(thresholdSeconds: number = 60): boolean {
  if (!tokenExpiresAt) return true
  const threshold = thresholdSeconds * 1000
  return Date.now() >= tokenExpiresAt - threshold
}

/**
 * Refresh the access token using the refresh token cookie
 * This calls the backend refresh endpoint which reads the HttpOnly cookie
 */
export async function refreshAccessToken(): Promise<string | null> {
  // If a refresh is already in progress, wait for it
  if (refreshPromise) {
    return refreshPromise
  }

  refreshPromise = performTokenRefresh()

  try {
    const token = await refreshPromise
    return token
  } finally {
    refreshPromise = null
  }
}

async function performTokenRefresh(): Promise<string | null> {
  try {
    console.log('[Auth] Attempting token refresh...')
    console.log('[Auth] Current location:', window.location.href)

    const response = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      credentials: 'include', // Important: Include cookies
      headers: {
        'Content-Type': 'application/json',
      },
    })

    console.log('[Auth] Refresh response status:', response.status)
    console.log('[Auth] Response headers:', Object.fromEntries(response.headers.entries()))

    if (!response.ok) {
      const errorText = await response.text()
      console.log('[Auth] Refresh failed with status', response.status, ':', errorText)
      clearAccessToken()
      return null
    }

    const data = (await response.json()) as { accessToken: string; expiresIn: number }
    console.log('[Auth] Refresh successful, token expires in:', data.expiresIn, 'seconds')
    setAccessToken(data.accessToken, data.expiresIn)
    return data.accessToken
  } catch (error) {
    console.error('[Auth] Refresh error:', error)
    clearAccessToken()
    return null
  }
}

/**
 * Get a valid access token, refreshing if necessary
 */
export async function getValidAccessToken(): Promise<string | null> {
  // If we have a valid token that's not expiring soon, return it
  if (hasValidAccessToken() && !isTokenExpiringSoon()) {
    return accessToken
  }

  // Try to refresh the token
  return refreshAccessToken()
}

/**
 * Logout - clear token and call logout endpoint
 */
export async function logout(): Promise<void> {
  try {
    await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      },
    })
  } finally {
    clearAccessToken()
  }
}

/**
 * Check if user is authenticated by attempting to get a valid token
 */
export async function checkAuth(): Promise<boolean> {
  const token = await getValidAccessToken()
  return token !== null
}

/**
 * Auth event types for listeners
 */
export type AuthEventType = 'login' | 'logout' | 'token_refreshed' | 'token_expired'

type AuthEventCallback = (event: AuthEventType) => void

const authEventListeners: AuthEventCallback[] = []

/**
 * Subscribe to auth events
 */
export function onAuthEvent(callback: AuthEventCallback): () => void {
  authEventListeners.push(callback)
  return () => {
    const index = authEventListeners.indexOf(callback)
    if (index > -1) {
      authEventListeners.splice(index, 1)
    }
  }
}

/**
 * Emit an auth event
 */
export function emitAuthEvent(event: AuthEventType): void {
  authEventListeners.forEach((callback) => callback(event))
}
