/**
 * Pending Session Management
 *
 * Manages the session data for users whose accounts are pending approval.
 * Uses sessionStorage to persist data during the browser session with a 48-hour expiry.
 */

const PENDING_SESSION_KEY = 'webstock-pending-session'
const EXPIRY_HOURS = 48

interface PendingSession {
  email: string
  pendingToken: string
  expiresAt: number
}

/**
 * Store a pending session in sessionStorage
 */
export function setPendingSession(session: Omit<PendingSession, 'expiresAt'>): void {
  const expiresAt = Date.now() + EXPIRY_HOURS * 60 * 60 * 1000
  const data: PendingSession = {
    ...session,
    expiresAt,
  }

  try {
    sessionStorage.setItem(PENDING_SESSION_KEY, JSON.stringify(data))
  } catch (error) {
    // Safari private mode or storage full - silently fail
    console.warn('[PendingSession] Failed to store session:', error)
  }
}

/**
 * Get the current pending session, or null if expired/missing
 */
export function getPendingSession(): PendingSession | null {
  try {
    const stored = sessionStorage.getItem(PENDING_SESSION_KEY)
    if (!stored) return null

    const session = JSON.parse(stored) as PendingSession

    // Check if expired
    if (Date.now() >= session.expiresAt) {
      clearPendingSession()
      return null
    }

    return session
  } catch {
    // Invalid JSON or storage error
    clearPendingSession()
    return null
  }
}

/**
 * Clear the pending session from storage
 */
export function clearPendingSession(): void {
  try {
    sessionStorage.removeItem(PENDING_SESSION_KEY)
  } catch {
    // Silently fail - storage might be unavailable
  }
}

/**
 * Check if there is a valid pending session
 */
export function hasPendingSession(): boolean {
  return getPendingSession() !== null
}
