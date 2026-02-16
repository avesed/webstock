import { useRegisterSW } from 'virtual:pwa-register/react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { X, RefreshCw, Wifi } from 'lucide-react'
import { useState, useEffect, useRef } from 'react'

const UPDATE_CHECK_INTERVAL = 60 * 60 * 1000 // 1 hour

export function PWAUpdatePrompt() {
  const { t } = useTranslation('common')
  const [dismissed, setDismissed] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const {
    needRefresh: [needRefresh, setNeedRefresh],
    offlineReady: [offlineReady, setOfflineReady],
    updateServiceWorker,
  } = useRegisterSW({
    onRegistered(registration) {
      if (registration) {
        // Periodically check for updates
        intervalRef.current = setInterval(() => {
          registration.update().catch((err: unknown) => {
            console.warn('[PWA] Update check failed:', err)
          })
        }, UPDATE_CHECK_INTERVAL)
      }
    },
    onRegisterError(error) {
      console.error('[PWA] Registration error:', error)
    },
  })

  // Clean up interval on unmount
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [])

  const close = () => {
    setOfflineReady(false)
    setNeedRefresh(false)
    setDismissed(true)
  }

  if (dismissed || (!offlineReady && !needRefresh)) return null

  return (
    <div className="fixed bottom-20 left-4 right-4 z-50 sm:bottom-4 sm:left-auto sm:right-4 sm:w-auto sm:min-w-[320px]">
      <div className="flex items-center gap-3 rounded-lg border bg-card p-4 shadow-lg">
        {offlineReady ? (
          <Wifi className="h-5 w-5 shrink-0 text-green-500" />
        ) : (
          <RefreshCw className="h-5 w-5 shrink-0 text-primary" />
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">
            {offlineReady ? t('pwa.offlineReady') : t('pwa.updateAvailable')}
          </p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {offlineReady ? t('pwa.offlineReadyDesc') : t('pwa.updateAvailableDesc')}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {needRefresh && (
            <Button size="sm" onClick={() => updateServiceWorker(true)}>
              {t('pwa.reload')}
            </Button>
          )}
          <Button variant="ghost" size="icon" className="h-8 w-8" onClick={close}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
