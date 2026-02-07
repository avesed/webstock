import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { SecondaryTabsList, SecondaryTabsTrigger } from '@/components/ui/nested-tabs'
import { AITabChat } from './AITabChat'
import { AITabExtension } from './AITabExtension'
import type { AISubTab } from '@/hooks/useTabNavigation'

// Lazy load AnalysisPanel for code splitting (it has heavy dependencies like dompurify)
const AnalysisPanel = lazy(() => import('@/components/analysis/AnalysisPanel'))

interface AITabProps {
  symbol: string
  subTab: AISubTab
  onSubTabChange: (tab: AISubTab) => void
}

/**
 * AI features tab container.
 * Contains Analysis, Chat, and Extension sub-tabs.
 * Uses lazy loading for AnalysisPanel to optimize initial bundle size.
 */
export function AITab({ symbol, subTab, onSubTabChange }: AITabProps) {
  const { t } = useTranslation('dashboard')

  return (
    <Tabs
      value={subTab}
      onValueChange={(v) => onSubTabChange(v as AISubTab)}
    >
      <SecondaryTabsList>
        <SecondaryTabsTrigger value="analysis">
          {t('stock.analysis')}
        </SecondaryTabsTrigger>
        <SecondaryTabsTrigger value="chat">
          {t('stock.tabs.chat', 'Chat')}
        </SecondaryTabsTrigger>
        <SecondaryTabsTrigger value="extension">
          {t('stock.tabs.extension', 'Extensions')}
        </SecondaryTabsTrigger>
      </SecondaryTabsList>

      <TabsContent value="analysis" className="mt-4">
        <Suspense fallback={<AnalysisFallback />}>
          <AnalysisPanel symbol={symbol} />
        </Suspense>
      </TabsContent>

      <TabsContent value="chat" className="mt-4">
        <AITabChat />
      </TabsContent>

      <TabsContent value="extension" className="mt-4">
        <AITabExtension />
      </TabsContent>
    </Tabs>
  )
}

function AnalysisFallback() {
  return (
    <div className="flex min-h-[200px] items-center justify-center py-12">
      <div className="flex flex-col items-center gap-3">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="text-sm text-muted-foreground">Loading analysis panel...</span>
      </div>
    </div>
  )
}
