import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Newspaper, TrendingUp, Clock, Loader2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { newsApi } from '@/api'
import NewsFeed from './NewsFeed'
import NewsCard from './NewsCard'

interface NewsPageContentProps {
  className?: string
}

export default function NewsPageContent({ className }: NewsPageContentProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')
  const [activeTab, setActiveTab] = useState('feed')

  // Fetch trending news for the sidebar
  const {
    data: trendingNewsData,
    isLoading: isTrendingLoading,
  } = useQuery({
    queryKey: ['news', 'trending'],
    queryFn: newsApi.getTrending,
  })

  // Ensure trendingNews is always an array with valid items
  const trendingNews = Array.isArray(trendingNewsData)
    ? trendingNewsData.filter((item): item is NonNullable<typeof item> =>
        item != null && typeof item === 'object' && 'id' in item
      )
    : []

  // Fetch stats
  const {
    data: feedData,
  } = useQuery({
    queryKey: ['news', 'feed-stats'],
    queryFn: () => newsApi.getFeed(1, 1),
  })

  const handleSymbolClick = useCallback((symbol: string) => {
    navigate(`/stock/${symbol}`)
  }, [navigate])

  const todayArticles = feedData?.total ?? 0

  return (
    <div className={cn('space-y-6', className)}>
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('news.title')}</h1>
        <p className="text-muted-foreground">
          {t('news.subtitle')}
        </p>
      </div>

      {/* Stats cards */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('news.totalArticles')}</CardTitle>
            <Newspaper className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{todayArticles}</div>
            <p className="text-xs text-muted-foreground">{t('news.articlesAvailable')}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('news.trending')}</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{trendingNews?.length ?? 0}</div>
            <p className="text-xs text-muted-foreground">{t('news.hotTopics')}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('news.lastUpdate')}</CardTitle>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">Live</div>
            <p className="text-xs text-muted-foreground">{t('news.realTimeUpdates')}</p>
          </CardContent>
        </Card>
      </div>

      {/* Main content */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* News feed - main area */}
        <div className="lg:col-span-2">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
          <TabsList>
            <TabsTrigger value="feed">{t('news.myFeed')}</TabsTrigger>
            <TabsTrigger value="trending">{t('news.trending')}</TabsTrigger>
            <TabsTrigger value="market">{t('news.market')}</TabsTrigger>
          </TabsList>

            <TabsContent value="feed">
              <Card>
                <CardHeader>
                  <CardTitle>{t('news.yourFeed')}</CardTitle>
                  <CardDescription>
                    {t('news.feedDescription')}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  <NewsFeed mode="feed" maxHeight="600px" />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="trending">
              <Card>
                <CardHeader>
                  <CardTitle>{t('news.trendingTitle')}</CardTitle>
                  <CardDescription>{t('news.trendingDescription')}</CardDescription>
                </CardHeader>
                <CardContent>
                  <NewsFeed mode="trending" maxHeight="600px" />
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="market">
              <Card>
                <CardHeader>
                  <CardTitle>{t('news.marketOverview')}</CardTitle>
                  <CardDescription>{t('news.marketDescription')}</CardDescription>
                </CardHeader>
                <CardContent>
                  <NewsFeed mode="market" maxHeight="600px" />
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>

        {/* Trending sidebar */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center gap-2">
                <TrendingUp className="h-5 w-5 text-primary" />
                <CardTitle className="text-lg">{t('news.trendingNow')}</CardTitle>
              </div>
              <CardDescription>{t('news.topStories')}</CardDescription>
            </CardHeader>
            <CardContent>
              {isTrendingLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : trendingNews && trendingNews.length > 0 ? (
                <div className="space-y-1">
                  {trendingNews.slice(0, 5).map((article) => (
                    <NewsCard
                      key={article.id}
                      article={article}
                      compact
                      onSymbolClick={handleSymbolClick}
                    />
                  ))}
                </div>
              ) : (
                <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
                  <Newspaper className="h-8 w-8 mb-2 opacity-50" />
                  <p className="text-sm">{t('news.noNews')}</p>
                </div>
              )}
            </CardContent>
          </Card>

          {/* Quick links to popular stocks */}
          <Card>
            <CardHeader>
              <CardTitle className="text-lg">{t('news.popularStocks')}</CardTitle>
              <CardDescription>{t('news.viewNewsByStock')}</CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-2">
                {['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA', 'META', 'NVDA'].map((symbol) => (
                  <button
                    key={symbol}
                    onClick={() => handleSymbolClick(symbol)}
                    className="inline-flex items-center rounded-full bg-muted px-3 py-1 text-sm font-medium hover:bg-muted/80 transition-colors"
                  >
                    {symbol}
                  </button>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}
