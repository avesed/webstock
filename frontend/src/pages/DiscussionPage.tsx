import { Suspense, lazy } from 'react'
import { useParams, Navigate, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'

const DiscussionPanel = lazy(() => import('@/components/discussion/DiscussionPanel'))

/**
 * Full-screen discussion page for mobile/PWA.
 * Navigated to from DiscussionPanel when on mobile — auto-starts the discussion.
 * Uses negative margins to fill the MainLayout content area edge-to-edge.
 */
export default function DiscussionPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation('common')

  if (!symbol) {
    return <Navigate to="/" replace />
  }

  return (
    <div className="flex flex-col h-full -mx-4 -mt-4 lg:-mx-6 lg:-mt-6">
      {/* Thin header with back button and symbol */}
      <div className="flex items-center gap-2 border-b bg-card px-3 py-2.5 shrink-0">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          onClick={() => {
            if (window.history.length > 1) {
              navigate(-1)
            } else {
              navigate(`/stock/${encodeURIComponent(symbol)}`)
            }
          }}
        >
          <ArrowLeft className="h-5 w-5" />
        </Button>
        <span className="font-semibold text-sm">{symbol.toUpperCase()}</span>
        <span className="text-xs text-muted-foreground">{t('discussion.title')}</span>
      </div>

      {/* DiscussionPanel fills remaining space */}
      <div className="flex-1 overflow-y-auto p-4">
        <Suspense
          fallback={
            <div className="flex h-40 items-center justify-center">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          }
        >
          <DiscussionPanel symbol={symbol} autoStart />
        </Suspense>
      </div>
    </div>
  )
}
