import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, useEffect, useCallback, useRef } from 'react'
import Markdown from 'react-markdown'
import {
  TrendingUp,
  TrendingDown,
  Building2,
  Globe,
  Users,
  Calendar,
  Plus,
  Check,
  ExternalLink,
  Loader2,
  AlertCircle,
  RefreshCw,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { StockChart, ChartControls, useChartControls } from '@/components/chart'
import { cn } from '@/lib/utils'
import {
  formatCurrency,
  formatCompactNumber,
  formatPercent,
  formatDate,
  getPriceChangeColor,
} from '@/lib/utils'
import { stockApi, watchlistApi, newsApi, analysisApi } from '@/api'
import { useStockStore } from '@/stores/stockStore'
import { useToast } from '@/hooks'
import { StockChatWidget } from '@/components/chat'
import type { StockQuote, StockInfo, StockFinancials, NewsArticle } from '@/types'

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { setSelectedSymbol, setSelectedQuote, setSelectedInfo } = useStockStore()
  const chartControls = useChartControls('1M')
  const [analysisContent, setAnalysisContent] = useState('')
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  const upperSymbol = symbol?.toUpperCase() ?? ''

  // Fetch stock quote
  const {
    data: quote,
    isLoading: isLoadingQuote,
    error: quoteError,
    refetch: refetchQuote,
  } = useQuery({
    queryKey: ['stock-quote', upperSymbol],
    queryFn: () => stockApi.getQuote(upperSymbol),
    enabled: !!upperSymbol,
    refetchInterval: 30000, // Refresh every 30 seconds
  })

  // Fetch stock info
  const {
    data: info,
    isLoading: isLoadingInfo,
  } = useQuery({
    queryKey: ['stock-info', upperSymbol],
    queryFn: () => stockApi.getInfo(upperSymbol),
    enabled: !!upperSymbol,
  })

  // Fetch stock financials
  const {
    data: financials,
    isLoading: isLoadingFinancials,
  } = useQuery({
    queryKey: ['stock-financials', upperSymbol],
    queryFn: () => stockApi.getFinancials(upperSymbol),
    enabled: !!upperSymbol,
  })

  // Fetch chart data (auto-refresh for intraday timeframes)
  const isIntradayTimeframe = chartControls.timeframe === '1H' || chartControls.timeframe === '1D'
  const {
    data: chartData,
    isLoading: isLoadingChart,
  } = useQuery({
    queryKey: ['stock-history', upperSymbol, chartControls.timeframe],
    queryFn: () => stockApi.getHistory(upperSymbol, chartControls.timeframe),
    enabled: !!upperSymbol,
    refetchInterval: isIntradayTimeframe ? 60000 : false, // Refresh every 60s for 1H/1D
  })

  // Fetch related news
  const {
    data: newsData,
    isLoading: isLoadingNews,
  } = useQuery({
    queryKey: ['stock-news', upperSymbol],
    queryFn: () => newsApi.getBySymbol(upperSymbol, 1, 10),
    enabled: !!upperSymbol,
  })

  // Fetch watchlists to check if stock is in any
  const { data: watchlistsData } = useQuery({
    queryKey: ['watchlists'],
    queryFn: watchlistApi.getAll,
  })

  // Ensure watchlists is always an array
  const watchlists = Array.isArray(watchlistsData) ? watchlistsData : []

  // Update store when data changes
  useEffect(() => {
    if (upperSymbol) {
      setSelectedSymbol(upperSymbol)
    }
    return () => {
      setSelectedSymbol(null)
      setSelectedQuote(null)
      setSelectedInfo(null)
    }
  }, [upperSymbol, setSelectedSymbol, setSelectedQuote, setSelectedInfo])

  useEffect(() => {
    if (quote) setSelectedQuote(quote)
  }, [quote, setSelectedQuote])

  useEffect(() => {
    if (info) setSelectedInfo(info)
  }, [info, setSelectedInfo])

  // Add to watchlist mutation
  const addToWatchlistMutation = useMutation({
    mutationFn: ({ watchlistId, symbol }: { watchlistId: number | string; symbol: string }) =>
      watchlistApi.addSymbol(watchlistId, symbol),
    onSuccess: (_, { symbol }) => {
      queryClient.invalidateQueries({ queryKey: ['watchlists'] })
      toast({
        title: 'Added to watchlist',
        description: `${symbol} has been added to your watchlist.`,
      })
    },
    onError: (error: unknown, { symbol }) => {
      // Check for 409 Conflict error (stock already in watchlist)
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { status?: number; data?: { detail?: string } } }
        if (axiosError.response?.status === 409) {
          toast({
            title: 'Already added',
            description: `${symbol} is already in this watchlist.`,
            variant: 'default',
          })
          return
        }
      }
      toast({
        title: 'Error',
        description: 'Failed to add stock to watchlist.',
        variant: 'destructive',
      })
    },
  })

  // Cleanup EventSource on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort()
        abortRef.current = null
      }
    }
  }, [])

  // Handle streaming AI analysis
  const handleAnalyze = useCallback(() => {
    if (!upperSymbol || isAnalyzing) return

    // Cancel any existing stream
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }

    setIsAnalyzing(true)
    setAnalysisContent('')

    const controller = analysisApi.streamAnalysis(
      upperSymbol,
      (data) => {
        const type = data.type as string
        if (type === 'agent_chunk' && data.content) {
          setAnalysisContent((prev) => prev + (data.content as string))
        } else if (type === 'complete') {
          setIsAnalyzing(false)
          abortRef.current = null
        } else if (type === 'error' || type === 'timeout') {
          setIsAnalyzing(false)
          abortRef.current = null
          toast({
            title: 'Analysis Error',
            description: (data.error as string) || 'Failed to generate analysis. Please try again.',
            variant: 'destructive',
          })
        }
      },
      () => {
        setIsAnalyzing(false)
        abortRef.current = null
      },
      () => {
        setIsAnalyzing(false)
        abortRef.current = null
      },
    )
    abortRef.current = controller
  }, [upperSymbol, isAnalyzing, toast])

  // Check if stock is in a watchlist
  const isInWatchlist = (watchlistId: number | string): boolean => {
    const watchlist = watchlists?.find((w) => w.id === watchlistId)
    return watchlist?.symbols?.includes(upperSymbol) ?? false
  }

  // Handle add to watchlist
  const handleAddToWatchlist = (watchlistId: number | string) => {
    if (!isInWatchlist(watchlistId)) {
      addToWatchlistMutation.mutate({ watchlistId, symbol: upperSymbol })
    }
  }

  if (quoteError) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-16">
        <AlertCircle className="h-12 w-12 text-destructive" />
        <h2 className="text-xl font-semibold">Failed to load stock data</h2>
        <p className="text-muted-foreground">
          Could not find data for symbol "{upperSymbol}"
        </p>
        <Button onClick={() => refetchQuote()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Try again
        </Button>
      </div>
    )
  }

  const priceChange = quote?.change ?? 0
  const priceChangePercent = quote?.changePercent ?? 0

  return (
    <div className="space-y-6">
      {/* Header section */}
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">{upperSymbol}</h1>
            {quote?.market && (
              <span className="rounded bg-muted px-2 py-0.5 text-sm font-medium text-muted-foreground">
                {quote.market}
              </span>
            )}
          </div>
          {(info?.name || quote?.name) && (
            <p className="text-lg text-muted-foreground">{info?.name ?? quote?.name}</p>
          )}
        </div>

        {/* Price display */}
        {isLoadingQuote ? (
          <div className="flex items-center gap-2">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-muted-foreground">Loading price...</span>
          </div>
        ) : quote ? (
          <div className="text-right">
            <div className="text-3xl font-bold">{formatCurrency(quote.price)}</div>
            <div
              className={cn(
                'flex items-center justify-end gap-1 text-lg font-medium',
                getPriceChangeColor(priceChange)
              )}
            >
              {priceChange >= 0 ? (
                <TrendingUp className="h-5 w-5" />
              ) : (
                <TrendingDown className="h-5 w-5" />
              )}
              <span>
                {priceChange >= 0 ? '+' : ''}{formatCurrency(Math.abs(priceChange))} ({formatPercent(priceChangePercent)})
              </span>
            </div>
            <p className="text-sm text-muted-foreground">
              As of {formatDate(quote.timestamp, { hour: 'numeric', minute: 'numeric' })}
            </p>
          </div>
        ) : null}
      </div>

      {/* Action buttons */}
      <div className="flex gap-2">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline">
              <Plus className="mr-2 h-4 w-4" />
              Add to Watchlist
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-48">
            {watchlists && watchlists.length > 0 ? (
              watchlists.map((watchlist) => (
                <DropdownMenuItem
                  key={watchlist.id}
                  onClick={() => handleAddToWatchlist(watchlist.id)}
                  disabled={isInWatchlist(watchlist.id)}
                >
                  {isInWatchlist(watchlist.id) ? (
                    <Check className="mr-2 h-4 w-4 text-stock-up" />
                  ) : (
                    <Plus className="mr-2 h-4 w-4" />
                  )}
                  {watchlist.name}
                </DropdownMenuItem>
              ))
            ) : (
              <DropdownMenuItem disabled>
                No watchlists available
              </DropdownMenuItem>
            )}
          </DropdownMenuContent>
        </DropdownMenu>

        <Button variant="outline" onClick={() => refetchQuote()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
      </div>

      {/* Main content grid */}
      <div className="grid gap-6 lg:grid-cols-3">
        {/* Chart area - takes 2 columns */}
        <div className="lg:col-span-2 space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <div>
                <CardTitle>Price Chart</CardTitle>
                <CardDescription>Interactive candlestick chart with volume</CardDescription>
              </div>
              <ChartControls
                timeframe={chartControls.timeframe}
                onTimeframeChange={chartControls.setTimeframe}
              />
            </CardHeader>
            <CardContent>
              <StockChart
                data={chartData ?? []}
                timeframe={chartControls.timeframe}
                symbol={upperSymbol}
                isLoading={isLoadingChart}
                height={400}
                onTimeframeChange={chartControls.setTimeframe}
              />
            </CardContent>
          </Card>

          {/* Tabs for Analysis, Financials, News */}
          <Tabs defaultValue="analysis" className="space-y-4">
            <TabsList className="grid w-full grid-cols-3">
              <TabsTrigger value="analysis">AI Analysis</TabsTrigger>
              <TabsTrigger value="financials">Financials</TabsTrigger>
              <TabsTrigger value="news">News</TabsTrigger>
            </TabsList>

            <TabsContent value="analysis">
              <Card>
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <div>
                      <CardTitle>AI Analysis</CardTitle>
                      <CardDescription>
                        Comprehensive AI-powered analysis including fundamental, technical, and sentiment insights
                      </CardDescription>
                    </div>
                    <Button onClick={handleAnalyze} disabled={isAnalyzing}>
                      {isAnalyzing ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Analyzing...
                        </>
                      ) : (
                        'Generate Analysis'
                      )}
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  {analysisContent ? (
                    <div className="prose prose-sm dark:prose-invert max-w-none">
                      <Markdown>{analysisContent.replace(/```json[\s\S]*?```\s*$/g, '').trim()}</Markdown>
                      {isAnalyzing && (
                        <span className="inline-block h-4 w-2 animate-pulse bg-primary" />
                      )}
                    </div>
                  ) : (
                    <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                      Click "Generate Analysis" to get AI-powered insights for {upperSymbol}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="financials">
              <Card>
                <CardHeader>
                  <CardTitle>Financial Data</CardTitle>
                  <CardDescription>Key financial metrics and ratios</CardDescription>
                </CardHeader>
                <CardContent>
                  {isLoadingFinancials ? (
                    <div className="flex h-[200px] items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin" />
                    </div>
                  ) : financials ? (
                    <FinancialsGrid financials={financials} />
                  ) : (
                    <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                      No financial data available
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="news">
              <Card>
                <CardHeader>
                  <CardTitle>Related News</CardTitle>
                  <CardDescription>Latest news articles for {upperSymbol}</CardDescription>
                </CardHeader>
                <CardContent>
                  {isLoadingNews ? (
                    <div className="flex h-[200px] items-center justify-center">
                      <Loader2 className="h-6 w-6 animate-spin" />
                    </div>
                  ) : newsData?.items && newsData.items.length > 0 ? (
                    <NewsList articles={newsData.items} />
                  ) : (
                    <div className="flex h-[200px] items-center justify-center text-muted-foreground">
                      No news articles available
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>

        {/* Sidebar - Stock Info */}
        <div className="space-y-4">
          {/* Key stats */}
          <Card>
            <CardHeader>
              <CardTitle>Key Statistics</CardTitle>
            </CardHeader>
            <CardContent>
              {isLoadingQuote ? (
                <div className="flex h-[200px] items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : quote ? (
                <StatsGrid quote={quote} />
              ) : null}
            </CardContent>
          </Card>

          {/* Company info */}
          <Card>
            <CardHeader>
              <CardTitle>Company Info</CardTitle>
            </CardHeader>
            <CardContent>
              {isLoadingInfo ? (
                <div className="flex h-[200px] items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin" />
                </div>
              ) : info ? (
                <CompanyInfo info={info} />
              ) : (
                <div className="flex h-[100px] items-center justify-center text-muted-foreground">
                  No company info available
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      {/* Floating AI Chat Widget */}
      <StockChatWidget symbol={upperSymbol} />
    </div>
  )
}

// Stats grid component
function StatsGrid({ quote }: { quote: StockQuote }) {
  const stats = [
    { label: 'Open', value: quote.open != null ? formatCurrency(quote.open) : 'N/A' },
    { label: 'Previous Close', value: quote.previousClose != null ? formatCurrency(quote.previousClose) : 'N/A' },
    { label: 'Day High', value: quote.dayHigh != null ? formatCurrency(quote.dayHigh) : 'N/A' },
    { label: 'Day Low', value: quote.dayLow != null ? formatCurrency(quote.dayLow) : 'N/A' },
    { label: 'Volume', value: quote.volume != null ? formatCompactNumber(quote.volume) : 'N/A' },
    {
      label: 'Market Cap',
      value: quote.marketCap ? formatCompactNumber(quote.marketCap) : 'N/A',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-4">
      {stats.map((stat) => (
        <div key={stat.label}>
          <p className="text-sm text-muted-foreground">{stat.label}</p>
          <p className="font-medium">{stat.value}</p>
        </div>
      ))}
    </div>
  )
}

// Financials grid component
function FinancialsGrid({ financials }: { financials: StockFinancials }) {
  const metrics = [
    {
      label: 'P/E Ratio',
      value: financials.peRatio?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'P/B Ratio',
      value: financials.pbRatio?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'EPS',
      value: financials.eps ? formatCurrency(financials.eps) : 'N/A',
    },
    {
      label: 'EPS Growth',
      value: financials.epsGrowth ? formatPercent(financials.epsGrowth) : 'N/A',
    },
    {
      label: 'Revenue',
      value: financials.revenue ? formatCompactNumber(financials.revenue) : 'N/A',
    },
    {
      label: 'Revenue Growth',
      value: financials.revenueGrowth ? formatPercent(financials.revenueGrowth) : 'N/A',
    },
    {
      label: 'Net Income',
      value: financials.netIncome ? formatCompactNumber(financials.netIncome) : 'N/A',
    },
    {
      label: 'Net Margin',
      value: financials.netMargin ? formatPercent(financials.netMargin) : 'N/A',
    },
    {
      label: 'ROE',
      value: financials.roe ? formatPercent(financials.roe) : 'N/A',
    },
    {
      label: 'ROA',
      value: financials.roa ? formatPercent(financials.roa) : 'N/A',
    },
    {
      label: 'Debt/Equity',
      value: financials.debtToEquity?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'Dividend Yield',
      value: financials.dividendYield ? formatPercent(financials.dividendYield) : 'N/A',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
      {metrics.map((metric) => (
        <div key={metric.label} className="space-y-1">
          <p className="text-sm text-muted-foreground">{metric.label}</p>
          <p className="font-medium">{metric.value}</p>
        </div>
      ))}
    </div>
  )
}

// Company info component
function CompanyInfo({ info }: { info: StockInfo }) {
  return (
    <div className="space-y-4">
      {info.description && (
        <p className="text-sm text-muted-foreground line-clamp-4">{info.description}</p>
      )}

      <Separator />

      <div className="space-y-3">
        {info.sector && (
          <div className="flex items-center gap-2">
            <Building2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">
              {info.sector}
              {info.industry && ` - ${info.industry}`}
            </span>
          </div>
        )}

        {info.website && (
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <a
              href={info.website}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-primary hover:underline flex items-center gap-1"
            >
              {new URL(info.website).hostname}
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        )}

        {info.employees && (
          <div className="flex items-center gap-2">
            <Users className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">{formatCompactNumber(info.employees)} employees</span>
          </div>
        )}

        {info.founded && (
          <div className="flex items-center gap-2">
            <Calendar className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">Founded {info.founded}</span>
          </div>
        )}

        {info.headquarters && (
          <p className="text-sm text-muted-foreground">{info.headquarters}</p>
        )}

        {info.ceo && (
          <p className="text-sm">
            <span className="text-muted-foreground">CEO:</span> {info.ceo}
          </p>
        )}
      </div>
    </div>
  )
}

// News list component
function NewsList({ articles }: { articles: NewsArticle[] }) {
  return (
    <div className="space-y-4">
      {articles.map((article) => (
        <a
          key={article.id}
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded-lg border p-4 transition-colors hover:bg-accent/50"
        >
          <div className="flex gap-4">
            {article.imageUrl && (
              <img
                src={article.imageUrl}
                alt=""
                className="h-20 w-20 rounded object-cover"
              />
            )}
            <div className="flex-1 min-w-0">
              <h4 className="font-medium line-clamp-2">{article.title}</h4>
              {article.summary && (
                <p className="mt-1 text-sm text-muted-foreground line-clamp-2">
                  {article.summary}
                </p>
              )}
              <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                <span>{article.source}</span>
                <span>-</span>
                <span>{formatDate(article.publishedAt)}</span>
                {article.sentiment && (
                  <span
                    className={cn(
                      'rounded px-1.5 py-0.5',
                      article.sentiment === 'POSITIVE'
                        ? 'bg-stock-up/10 text-stock-up'
                        : article.sentiment === 'NEGATIVE'
                        ? 'bg-stock-down/10 text-stock-down'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {article.sentiment.toLowerCase()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}
