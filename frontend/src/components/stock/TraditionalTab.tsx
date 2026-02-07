import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { SecondaryTabsList, SecondaryTabsTrigger } from '@/components/ui/nested-tabs'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { FinancialsGrid } from './FinancialsGrid'
import { NewsList } from './NewsList'
import type { StockFinancials, NewsArticle } from '@/types'
import type { TraditionalSubTab } from '@/hooks/useTabNavigation'

interface TraditionalTabProps {
  symbol: string
  isMetalAsset: boolean
  subTab: TraditionalSubTab
  onSubTabChange: (tab: TraditionalSubTab) => void
  financials: StockFinancials | undefined
  isLoadingFinancials: boolean
  newsArticles: NewsArticle[]
  isLoadingNews: boolean
}

/**
 * Traditional information tab container.
 * Contains Financials and News sub-tabs.
 * For metal assets, Financials tab is hidden.
 */
export function TraditionalTab({
  symbol,
  isMetalAsset,
  subTab,
  onSubTabChange,
  financials,
  isLoadingFinancials,
  newsArticles,
  isLoadingNews,
}: TraditionalTabProps) {
  const { t } = useTranslation('dashboard')

  // If metal asset and on financials tab, redirect to news
  const effectiveSubTab = isMetalAsset && subTab === 'financials' ? 'news' : subTab

  return (
    <Tabs
      value={effectiveSubTab}
      onValueChange={(v) => onSubTabChange(v as TraditionalSubTab)}
    >
      <SecondaryTabsList>
        {!isMetalAsset && (
          <SecondaryTabsTrigger value="financials">
            {t('stock.fundamentals')}
          </SecondaryTabsTrigger>
        )}
        <SecondaryTabsTrigger value="news">
          {t('stock.news')}
        </SecondaryTabsTrigger>
      </SecondaryTabsList>

      {!isMetalAsset && (
        <TabsContent value="financials" className="mt-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('stock.fundamentals')}</CardTitle>
              <CardDescription>{t('stock.financials.revenue')}</CardDescription>
            </CardHeader>
            <CardContent>
              {isLoadingFinancials ? (
                <LoadingPlaceholder />
              ) : financials ? (
                <FinancialsGrid financials={financials} />
              ) : (
                <EmptyPlaceholder message={t('common:status.noData', 'No data available')} />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      )}

      <TabsContent value="news" className="mt-4">
        <Card>
          <CardHeader>
            <CardTitle>{t('stock.news')}</CardTitle>
            <CardDescription>{t('news.bySymbol', { symbol })}</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoadingNews ? (
              <LoadingPlaceholder />
            ) : newsArticles.length > 0 ? (
              <NewsList articles={newsArticles} />
            ) : (
              <EmptyPlaceholder message={t('news.noNews')} />
            )}
          </CardContent>
        </Card>
      </TabsContent>
    </Tabs>
  )
}

function LoadingPlaceholder() {
  return (
    <div className="flex h-[200px] items-center justify-center">
      <Loader2 className="h-6 w-6 animate-spin" />
    </div>
  )
}

function EmptyPlaceholder({ message }: { message: string }) {
  return (
    <div className="flex h-[200px] items-center justify-center text-muted-foreground">
      {message}
    </div>
  )
}
