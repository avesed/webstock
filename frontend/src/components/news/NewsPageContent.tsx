import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import NewsFeed from './NewsFeed'
import NewsFilterBar, { type NewsFilters } from './NewsFilterBar'

interface NewsPageContentProps {
  className?: string
}

const VALID_TABS = ['feed', 'market'] as const
type NewsTab = (typeof VALID_TABS)[number]

const VALID_SENTIMENTS = ['bullish', 'bearish', 'neutral'] as const
const VALID_MARKETS = ['US', 'HK', 'CN'] as const

export default function NewsPageContent({ className }: NewsPageContentProps) {
  const { t } = useTranslation('dashboard')
  const [searchParams, setSearchParams] = useSearchParams()

  // Read tab from URL
  const rawTab = searchParams.get('tab')
  const activeTab: NewsTab = VALID_TABS.includes(rawTab as NewsTab) ? (rawTab as NewsTab) : 'feed'

  // Read filter state from URL (with validation)
  const rawSentiment = searchParams.get('sentiment')
  const rawMarket = searchParams.get('market')
  const filters: NewsFilters = {
    search: searchParams.get('q') ?? '',
    sentimentTag: VALID_SENTIMENTS.includes(rawSentiment as NewsFilters['sentimentTag'] & string)
      ? (rawSentiment as NewsFilters['sentimentTag'])
      : null,
    market: VALID_MARKETS.includes(rawMarket as NewsFilters['market'] & string)
      ? (rawMarket as NewsFilters['market'])
      : null,
  }

  // Convert to API filters (conditionally include truthy values only for exactOptionalPropertyTypes)
  const apiFilters = useMemo(() => {
    const f: { search?: string; sentimentTag?: string; market?: string } = {}
    if (filters.search) f.search = filters.search
    if (filters.sentimentTag) f.sentimentTag = filters.sentimentTag
    if (filters.market) f.market = filters.market
    return f
  }, [filters.search, filters.sentimentTag, filters.market])

  const handleTabChange = (value: string) => {
    // Tab switch clears all filters
    setSearchParams(value === 'feed' ? {} : { tab: value }, { replace: true })
  }

  const handleFiltersChange = (newFilters: NewsFilters) => {
    const params: Record<string, string> = {}
    if (activeTab !== 'feed') params.tab = activeTab
    if (newFilters.search) params.q = newFilters.search
    if (newFilters.sentimentTag) params.sentiment = newFilters.sentimentTag
    if (newFilters.market) params.market = newFilters.market
    setSearchParams(params, { replace: true })
  }

  return (
    <div className={cn('space-y-4', className)}>
      <Tabs value={activeTab} onValueChange={handleTabChange}>
        {/* Header row: title + tabs inline */}
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="text-2xl font-bold tracking-tight">{t('news.title')}</h1>
          <TabsList>
            <TabsTrigger value="feed">{t('news.myFeed')}</TabsTrigger>
            <TabsTrigger value="market">{t('news.market')}</TabsTrigger>
          </TabsList>
        </div>

        {/* Filter bar */}
        <NewsFilterBar
          filters={filters}
          onFiltersChange={handleFiltersChange}
          showMarketFilter={activeTab === 'market'}
          className="mt-3"
        />

        {/* Feed content */}
        <TabsContent value="feed" className="mt-4">
          <NewsFeed mode="feed" filters={apiFilters} />
        </TabsContent>

        <TabsContent value="market" className="mt-4">
          <NewsFeed mode="market" filters={apiFilters} />
        </TabsContent>
      </Tabs>
    </div>
  )
}
