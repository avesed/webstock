import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { TrendingUp, TrendingDown, Activity, BarChart3, Loader2, ArrowUpRight, ArrowDownRight } from 'lucide-react'
import { stockApi, watchlistApi, alertsApi, portfolioApi, newsApi } from '@/api'
import type { StockQuote, NewsArticle } from '@/types'
import { formatCurrency, cn } from '@/lib/utils'
import { useNavigate } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

interface MarketIndex {
  symbol: string
  name: string
  quote?: StockQuote
}

const MARKET_INDICES: MarketIndex[] = [
  { symbol: 'SPY', name: 'S&P 500' },
  { symbol: 'QQQ', name: 'NASDAQ' },
  { symbol: 'DIA', name: 'Dow Jones' },
]

export default function DashboardPage() {
  const { t } = useTranslation('dashboard')
  const navigate = useNavigate()
  const { isAuthenticated, isLoading: isAuthLoading } = useAuthStore()

  // Only fetch data when auth is ready to avoid 401 race conditions
  const canFetch = isAuthenticated && !isAuthLoading

  // Fetch watchlists count
  const { data: watchlists } = useQuery({
    queryKey: ['watchlists'],
    queryFn: watchlistApi.getAll,
    enabled: canFetch,
  })

  // Fetch alerts count
  const { data: alerts } = useQuery({
    queryKey: ['alerts'],
    queryFn: alertsApi.getAll,
    enabled: canFetch,
  })

  // Fetch portfolios
  const { data: portfolios } = useQuery({
    queryKey: ['portfolios'],
    queryFn: portfolioApi.getAll,
    enabled: canFetch,
  })

  // Fetch market indices
  const { data: marketQuotes, isLoading: isLoadingMarket } = useQuery({
    queryKey: ['market-indices'],
    queryFn: async () => {
      const quotes = await Promise.all(
        MARKET_INDICES.map(async (index) => {
          try {
            const quote = await stockApi.getQuote(index.symbol)
            return { ...index, quote }
          } catch {
            return index
          }
        })
      )
      return quotes
    },
    refetchInterval: 60000, // Refresh every minute
    enabled: canFetch,
  })

  // Fetch trending news (uses AKShare for Chinese market, no API key needed)
  const { data: newsData, isLoading: isLoadingNews } = useQuery({
    queryKey: ['news-trending'],
    queryFn: () => newsApi.getTrending(),
    enabled: canFetch,
  })

  // Calculate watchlist total stocks
  const totalWatchlistStocks = watchlists?.reduce((total, w) => {
    return total + (w.itemCount || w.symbols?.length || 0)
  }, 0) || 0

  // Calculate active alerts count
  const activeAlertsCount = alerts?.filter(a => a.status === 'ACTIVE').length || 0

  // Calculate portfolio stats
  const totalPortfolioValue = portfolios?.reduce((total, p) => total + (p.totalValue || 0), 0) || 0
  const totalPortfolioChange = portfolios?.reduce((total, p) => total + (p.totalGain || 0), 0) || 0
  const totalPortfolioChangePercent = totalPortfolioValue > 0
    ? (totalPortfolioChange / (totalPortfolioValue - totalPortfolioChange)) * 100
    : 0

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">
          {t('welcome', { name: 'WebStock' })}
        </p>
      </div>

      {/* Stats overview */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="cursor-pointer hover:bg-accent/50 transition-colors" onClick={() => navigate('/portfolio')}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('portfolio.totalValue')}</CardTitle>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatCurrency(totalPortfolioValue)}</div>
            <p className={cn(
              "text-xs",
              totalPortfolioChange >= 0 ? "text-green-500" : "text-red-500"
            )}>
              {totalPortfolioChange >= 0 ? '+' : ''}{formatCurrency(totalPortfolioChange)} ({totalPortfolioChangePercent.toFixed(2)}%)
            </p>
          </CardContent>
        </Card>

        <Card className="cursor-pointer hover:bg-accent/50 transition-colors" onClick={() => navigate('/portfolio')}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('portfolio.dayChange')}</CardTitle>
            <Activity className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className={cn(
              "text-2xl font-bold",
              totalPortfolioChange >= 0 ? "text-green-500" : "text-red-500"
            )}>
              {totalPortfolioChange >= 0 ? '+' : ''}{formatCurrency(totalPortfolioChange)}
            </div>
            <p className="text-xs text-muted-foreground">
              {portfolios?.length || 0} {t('portfolio.title').toLowerCase()}
            </p>
          </CardContent>
        </Card>

        <Card className="cursor-pointer hover:bg-accent/50 transition-colors" onClick={() => navigate('/watchlist')}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('watchlist.title')}</CardTitle>
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalWatchlistStocks}</div>
            <p className="text-xs text-muted-foreground">
              {watchlists?.length || 0} {t('watchlist.myWatchlists').toLowerCase()}
            </p>
          </CardContent>
        </Card>

        <Card className="cursor-pointer hover:bg-accent/50 transition-colors" onClick={() => navigate('/alerts')}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">{t('alerts.active')}</CardTitle>
            <TrendingDown className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{activeAlertsCount}</div>
            <p className="text-xs text-muted-foreground">
              {alerts?.length || 0} {t('alerts.title').toLowerCase()}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Main content area */}
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Market Overview */}
        <Card>
          <CardHeader>
            <CardTitle>{t('overview')}</CardTitle>
            <CardDescription>
              {t('market.us')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoadingMarket ? (
              <div className="flex h-[200px] items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : marketQuotes && marketQuotes.length > 0 ? (
              <div className="space-y-4">
                {marketQuotes.map((index) => (
                  <div
                    key={index.symbol}
                    className="flex items-center justify-between p-3 rounded-lg bg-accent/50 cursor-pointer hover:bg-accent transition-colors"
                    onClick={() => navigate(`/stock/${index.symbol}`)}
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary/10">
                        <span className="text-sm font-bold text-primary">{index.symbol.slice(0, 2)}</span>
                      </div>
                      <div>
                        <p className="font-medium">{index.name}</p>
                        <p className="text-sm text-muted-foreground">{index.symbol}</p>
                      </div>
                    </div>
                    <div className="text-right">
                      {index.quote ? (
                        <>
                          <p className="font-medium">{formatCurrency(index.quote.price)}</p>
                          <p className={cn(
                            "text-sm",
                            (index.quote.changePercent || 0) >= 0 ? "text-green-500" : "text-red-500"
                          )}>
                            {(index.quote.changePercent || 0) >= 0 ? '+' : ''}
                            {(index.quote.changePercent || 0).toFixed(2)}%
                            {(index.quote.changePercent || 0) >= 0 ? (
                              <ArrowUpRight className="inline h-3 w-3 ml-1" />
                            ) : (
                              <ArrowDownRight className="inline h-3 w-3 ml-1" />
                            )}
                          </p>
                        </>
                      ) : (
                        <p className="text-sm text-muted-foreground">{t('common:status.noData', 'No data')}</p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                {t('common:status.noData', 'Market data unavailable')}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent News */}
        <Card>
          <CardHeader>
            <CardTitle>{t('news.latest')}</CardTitle>
            <CardDescription>
              {t('news.title')}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {isLoadingNews ? (
              <div className="flex h-[200px] items-center justify-center">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : newsData && newsData.length > 0 ? (
              <div className="space-y-4 max-h-[400px] overflow-y-auto">
                {newsData.slice(0, 5).map((article: NewsArticle) => (
                  <div
                    key={article.id}
                    className={cn(
                      "p-3 rounded-lg bg-accent/50 transition-colors",
                      article.symbol && article.symbol !== 'MARKET' && "cursor-pointer hover:bg-accent"
                    )}
                    onClick={() => article.symbol && article.symbol !== 'MARKET' && navigate(`/stock/${article.symbol}`)}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <p className="font-medium line-clamp-2">{article.title}</p>
                        <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
                          <span>{article.source}</span>
                          {article.symbol && article.symbol !== 'MARKET' && (
                            <>
                              <span>•</span>
                              <span className="text-primary font-medium">{article.symbol}</span>
                            </>
                          )}
                        </div>
                      </div>
                      {article.sentiment && (
                        <span className={cn(
                          "text-xs px-2 py-1 rounded-full shrink-0",
                          article.sentiment === 'POSITIVE' && "bg-green-500/10 text-green-500",
                          article.sentiment === 'NEGATIVE' && "bg-red-500/10 text-red-500",
                          article.sentiment === 'NEUTRAL' && "bg-gray-500/10 text-gray-500"
                        )}>
                          {article.sentiment.toLowerCase()}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                {t('news.noNews')}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

    </div>
  )
}
