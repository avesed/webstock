import { Suspense, lazy, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { SecondaryTabsList, SecondaryTabsTrigger } from '@/components/ui/nested-tabs'
import { AITabExtension } from './AITabExtension'
import type { AISubTab } from '@/hooks/useTabNavigation'

// Lazy load heavy panels for code splitting
const AnalysisPanel = lazy(() => import('@/components/analysis/AnalysisPanel'))
const DiscussionPanel = lazy(() => import('@/components/discussion/DiscussionPanel'))

interface AITabProps {
  symbol: string
  subTab: AISubTab
  onSubTabChange: (tab: AISubTab) => void
}

/**
 * AI features tab container.
 * Contains Analysis, Extension, and Discussion sub-tabs.
 *
 * The Discussion TabsContent uses `forceMount` so that an active SSE stream
 * (multi-agent discussion) survives sub-tab switches. The panel is hidden
 * via `data-[state=inactive]:hidden` when the user is on another sub-tab.
 * Lazy activation ensures the chunk is only loaded after the first visit.
 */
export function AITab({ symbol, subTab, onSubTabChange }: AITabProps) {
  const { t } = useTranslation('dashboard')

  // Track first activation to avoid eager lazy-loading the DiscussionPanel chunk
  const [discussionActivated, setDiscussionActivated] = useState(subTab === 'discussion')
  useEffect(() => {
    if (subTab === 'discussion') setDiscussionActivated(true)
  }, [subTab])

  return (
    <Tabs
      value={subTab}
      onValueChange={(v) => onSubTabChange(v as AISubTab)}
    >
      <SecondaryTabsList>
        <SecondaryTabsTrigger value="analysis">
          {t('stock.analysis')}
        </SecondaryTabsTrigger>
        <SecondaryTabsTrigger value="discussion">
          {t('stock.tabs.discussion', 'Discussion')}
        </SecondaryTabsTrigger>
        <SecondaryTabsTrigger value="extension">
          {t('stock.tabs.extension', 'Extensions')}
        </SecondaryTabsTrigger>
      </SecondaryTabsList>

      <TabsContent value="analysis" className="mt-4">
        <Suspense fallback={<PanelFallback />}>
          <AnalysisPanel symbol={symbol} />
        </Suspense>
      </TabsContent>

      <TabsContent value="discussion" className="mt-4 data-[state=inactive]:hidden" forceMount>
        {discussionActivated && (
          <Suspense fallback={<PanelFallback />}>
            <DiscussionPanel symbol={symbol} />
          </Suspense>
        )}
      </TabsContent>

      <TabsContent value="extension" className="mt-4">
        <AITabExtension />
      </TabsContent>
    </Tabs>
  )
}

function PanelFallback() {
  return (
    <div className="flex min-h-[200px] items-center justify-center py-12">
      <div className="flex flex-col items-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="text-sm text-muted-foreground">Loading...</span>
      </div>
    </div>
  )
}
