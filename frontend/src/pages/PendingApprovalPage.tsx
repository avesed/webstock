import { useState, useEffect, useCallback, useRef } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { LineChart, Clock, CheckCircle2, XCircle, RefreshCw, Globe, Mail } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { authApi } from '@/api'
import { getErrorMessage } from '@/api/client'
import { getPendingSession, clearPendingSession } from '@/lib/pendingSession'
import { useLocale } from '@/hooks'
import { cn } from '@/lib/utils'
import type { Locale } from '@/hooks'
import type { CheckStatusResponse } from '@/types'

type StatusState = 'idle' | 'checking' | 'pending' | 'approved' | 'rejected' | 'expired'

// Exponential backoff intervals in milliseconds: 30s -> 45s -> 67s -> 100s -> 150s -> 225s -> 300s (max)
const BACKOFF_INTERVALS = [30000, 45000, 67000, 100000, 150000, 225000, 300000]
const MAX_AUTO_POLL_DURATION = 10 * 60 * 1000 // 10 minutes

export default function PendingApprovalPage() {
  const { t } = useTranslation('auth')
  const { t: tCommon } = useTranslation('common')
  const { locale, setLocale } = useLocale()
  const navigate = useNavigate()

  const [status, setStatus] = useState<StatusState>('idle')
  const [rejectionReason, setRejectionReason] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [autoPollEnabled, setAutoPollEnabled] = useState(true)

  const pollAttemptRef = useRef(0)
  const pollStartTimeRef = useRef<number | null>(null)
  const timeoutRef = useRef<number | null>(null)
  const redirectTimeoutRef = useRef<number | null>(null)
  const statusRef = useRef<StatusState>(status)

  // Get pending session data
  const session = getPendingSession()

  // Keep statusRef in sync with status state to avoid stale closures
  useEffect(() => {
    statusRef.current = status
  }, [status])

  const checkStatus = useCallback(async () => {
    if (!session) {
      setStatus('expired')
      return
    }

    setStatus('checking')
    setError(null)

    try {
      const response: CheckStatusResponse = await authApi.checkAccountStatus(
        session.email,
        session.pendingToken
      )

      if (response.status === 'active') {
        setStatus('approved')
        clearPendingSession()
        // Redirect to login after 2 seconds (stored in ref for cleanup)
        redirectTimeoutRef.current = window.setTimeout(() => {
          navigate('/login')
        }, 2000)
      } else if (response.status === 'rejected') {
        setStatus('rejected')
        setRejectionReason(response.rejectionReason ?? null)
        clearPendingSession()
      } else {
        setStatus('pending')
      }
    } catch (err) {
      const message = getErrorMessage(err)
      setError(message)
      setStatus('pending')
    }
  }, [session, navigate])

  // Set up auto-polling with exponential backoff
  useEffect(() => {
    if (!session || !autoPollEnabled) return

    // Initialize poll start time
    if (pollStartTimeRef.current === null) {
      pollStartTimeRef.current = Date.now()
    }

    const schedulePoll = () => {
      // Check if we've exceeded max auto-poll duration
      const elapsed = Date.now() - (pollStartTimeRef.current ?? Date.now())
      if (elapsed >= MAX_AUTO_POLL_DURATION) {
        setAutoPollEnabled(false)
        return
      }

      // Get next interval based on attempt count
      const intervalIndex = Math.min(pollAttemptRef.current, BACKOFF_INTERVALS.length - 1)
      const interval = BACKOFF_INTERVALS[intervalIndex]

      timeoutRef.current = window.setTimeout(async () => {
        pollAttemptRef.current += 1
        await checkStatus()

        // Only continue polling if still pending (use ref to avoid stale closure)
        if (statusRef.current === 'pending' || statusRef.current === 'idle') {
          schedulePoll()
        }
      }, interval)
    }

    // Initial check
    checkStatus().then(() => {
      // Use ref to avoid stale closure
      if (statusRef.current === 'pending' || statusRef.current === 'idle') {
        schedulePoll()
      }
    })

    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current)
      }
      if (redirectTimeoutRef.current !== null) {
        window.clearTimeout(redirectTimeoutRef.current)
      }
    }
  }, [session, autoPollEnabled, checkStatus])

  const handleManualCheck = () => {
    pollAttemptRef.current = 0
    pollStartTimeRef.current = Date.now()
    setAutoPollEnabled(true)
    checkStatus()
  }

  const languageOptions: { value: Locale; label: string }[] = [
    { value: 'en', label: tCommon('language.en') },
    { value: 'zh', label: tCommon('language.zh') },
  ]

  // Session expired state
  if (!session || status === 'expired') {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background p-4">
        <LanguageSwitcher
          locale={locale}
          setLocale={setLocale}
          options={languageOptions}
        />
        <Card className="w-full max-w-md">
          <CardHeader className="space-y-1 text-center">
            <Logo />
            <CardTitle className="text-2xl">{t('pending.title')}</CardTitle>
            <CardDescription>{t('pending.sessionExpired')}</CardDescription>
          </CardHeader>
          <CardFooter className="flex justify-center">
            <Button asChild>
              <Link to="/login">{t('pending.backToLogin')}</Link>
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <LanguageSwitcher
        locale={locale}
        setLocale={setLocale}
        options={languageOptions}
      />

      <Card className="w-full max-w-md">
        <CardHeader className="space-y-1 text-center">
          <Logo />
          <CardTitle className="text-2xl">{t('pending.title')}</CardTitle>
          <CardDescription className="flex items-center justify-center gap-2">
            <Mail className="h-4 w-4" />
            {session.email}
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-6">
          {/* Status Display */}
          <div
            className="flex flex-col items-center gap-4"
            role="status"
            aria-live="polite"
          >
            <StatusIcon status={status} />
            <StatusMessage
              status={status}
              rejectionReason={rejectionReason}
              t={t}
            />
          </div>

          {/* Error Display */}
          {error && (
            <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive text-center">
              {error}
            </div>
          )}

          {/* Auto-poll disabled notice */}
          {!autoPollEnabled && status === 'pending' && (
            <p className="text-sm text-muted-foreground text-center">
              {t('pending.autoPollStopped')}
            </p>
          )}
        </CardContent>

        <CardFooter className="flex flex-col space-y-3">
          {/* Pending state actions */}
          {(status === 'pending' || status === 'idle' || status === 'checking') && (
            <Button
              variant="outline"
              className="w-full"
              onClick={handleManualCheck}
              disabled={status === 'checking'}
            >
              {status === 'checking' ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  {t('pending.checking')}
                </>
              ) : (
                <>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {t('pending.checkStatus')}
                </>
              )}
            </Button>
          )}

          {/* Rejected state actions */}
          {status === 'rejected' && (
            <>
              <Button asChild variant="outline" className="w-full">
                <a href="mailto:support@example.com">
                  {t('pending.contactSupport')}
                </a>
              </Button>
              <Button asChild variant="ghost" className="w-full">
                <Link to="/login">{t('pending.backToLogin')}</Link>
              </Button>
            </>
          )}

          {/* Back to login for pending users */}
          {status === 'pending' && (
            <Button asChild variant="ghost" className="w-full">
              <Link to="/login">{t('pending.backToLogin')}</Link>
            </Button>
          )}
        </CardFooter>
      </Card>
    </div>
  )
}

// Helper Components
function Logo() {
  return (
    <div className="flex justify-center mb-4">
      <div className="flex items-center gap-2">
        <LineChart className="h-8 w-8 text-primary" />
        <span className="text-2xl font-bold">WebStock</span>
      </div>
    </div>
  )
}

function LanguageSwitcher({
  locale,
  setLocale,
  options,
}: {
  locale: Locale
  setLocale: (locale: Locale) => void
  options: { value: Locale; label: string }[]
}) {
  return (
    <div className="absolute top-4 right-4">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon">
            <Globe className="h-5 w-5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {options.map((option) => (
            <DropdownMenuItem
              key={option.value}
              onClick={() => setLocale(option.value)}
              className={cn(locale === option.value && 'bg-accent')}
            >
              {option.label}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

function StatusIcon({ status }: { status: StatusState }) {
  switch (status) {
    case 'checking':
      return (
        <div className="rounded-full bg-muted p-6">
          <RefreshCw className="h-12 w-12 text-muted-foreground animate-spin" />
        </div>
      )
    case 'approved':
      return (
        <div className="rounded-full bg-green-100 dark:bg-green-900 p-6">
          <CheckCircle2 className="h-12 w-12 text-green-600 dark:text-green-400" />
        </div>
      )
    case 'rejected':
      return (
        <div className="rounded-full bg-red-100 dark:bg-red-900 p-6">
          <XCircle className="h-12 w-12 text-red-600 dark:text-red-400" />
        </div>
      )
    default:
      return (
        <div className="rounded-full bg-amber-100 dark:bg-amber-900 p-6">
          <Clock className="h-12 w-12 text-amber-600 dark:text-amber-400" />
        </div>
      )
  }
}

function StatusMessage({
  status,
  rejectionReason,
  t,
}: {
  status: StatusState
  rejectionReason: string | null
  t: ReturnType<typeof useTranslation<'auth'>>['t']
}) {
  switch (status) {
    case 'checking':
      return (
        <p className="text-center text-muted-foreground">
          {t('pending.checking')}
        </p>
      )
    case 'approved':
      return (
        <p className="text-center text-green-600 dark:text-green-400 font-medium">
          {t('pending.approved')}
        </p>
      )
    case 'rejected':
      return (
        <div className="text-center space-y-2">
          <p className="text-red-600 dark:text-red-400 font-medium">
            {t('pending.rejected')}
          </p>
          {rejectionReason && (
            <p className="text-sm text-muted-foreground">
              {t('pending.rejectedReason', { reason: rejectionReason })}
            </p>
          )}
        </div>
      )
    default:
      return (
        <p className="text-center text-muted-foreground">
          {t('pending.subtitle')}
        </p>
      )
  }
}
