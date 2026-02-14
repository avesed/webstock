import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient, keepPreviousData } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import {
  TrendingUp,
  TrendingDown,
  Plus,
  Check,
  Loader2,
  AlertCircle,
  RefreshCw,
  Maximize2,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PrimaryTabsList, PrimaryTabsTrigger } from '@/components/ui/nested-tabs'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SimpleChart } from '@/components/chart'
import {
  cn,
  formatCurrency,
  formatPercent,
  formatDate,
  getPriceChangeColor,
  isMetal,
} from '@/lib/utils'
import { stockApi, watchlistApi, newsApi, synthesizeTodayBar, synthesizeIntradayUpdate } from '@/api'
import { useStockStore } from '@/stores/stockStore'
import { useToast } from '@/hooks'
import { useTabNavigation, type AISubTab } from '@/hooks/useTabNavigation'
import { StockChatWidget } from '@/components/chat'
import {
  StockStatsGrid,
  MetalStatsGrid,
  CommodityInfo,
  CompanyInfo,
  FinancialsGrid,
  NewsList,
  AITab,
  StockChatProvider,
} from '@/components/stock'
import { QlibFactorPanel } from '@/components/qlib/QlibFactorPanel'

export default function StockDetailPage() {
  const { t } = useTranslation('dashboard')
  const { symbol } = useParams<{ symbol: string }>()
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { setSelectedSymbol, setSelectedQuote, setSelectedInfo } = useStockStore()
  const navigate = useNavigate()
  const [lastBarTime, setLastBarTime] = useState<number>(0)

  // Simplified timeframe for area chart (no interval selector, no indicators)
  const SIMPLE_TIMEFRAMES = ['1D', '1W', '1M', '3M', '1Y', 'ALL'] as const
  type SimpleTimeframe = typeof SIMPLE_TIMEFRAMES[number]
  const SIMPLE_TIMEFRAME_INTERVAL: Record<SimpleTimeframe, string> = {
    '1D': '5m', '1W': '15m', '1M': '1h', '3M': '1d', '1Y': '1d', 'ALL': '1mo',
  }
  const [simpleTimeframe, setSimpleTimeframe] = useState<SimpleTimeframe>('1D')
  const simpleInterval = SIMPLE_TIMEFRAME_INTERVAL[simpleTimeframe]

  // Tab navigation with URL sync
  const {
    primaryTab,
    subTab,
    setPrimaryTab,
    setSubTab,
  } = useTabNavigation()

  const upperSymbol = symbol?.toUpperCase() ?? ''
  const isMetalAsset = isMetal(upperSymbol)

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

  // Fetch stock financials (disabled for metals - no fundamentals available)
  const {
    data: financials,
    isLoading: isLoadingFinancials,
  } = useQuery({
    queryKey: ['stock-financials', upperSymbol],
    queryFn: () => stockApi.getFinancials(upperSymbol),
    enabled: !!upperSymbol && !isMetalAsset,
  })

  // Intraday timeframes use sub-daily intervals (5m, 15m)
  const isIntradayTimeframe = simpleTimeframe === '1D' || simpleTimeframe === '1W'
  const {
    data: chartData,
    isLoading: isLoadingChart,
    error: chartError,
  } = useQuery({
    queryKey: ['stock-history', upperSymbol, simpleTimeframe, simpleInterval],
    queryFn: () => stockApi.getHistory(upperSymbol, simpleTimeframe, {
      intervalOverride: simpleInterval,
    }),
    enabled: !!upperSymbol,
    placeholderData: keepPreviousData,
    refetchInterval: false,
    staleTime: isIntradayTimeframe ? 60_000 : 5 * 60_000,
    retry: (failureCount, error: any) => {
      // Don't retry 409 (futures market closed)
      if (error?.response?.status === 409) return false
      return failureCount < 2
    },
  })

  // Track the timestamp of the last bar for incremental fetching
  useEffect(() => {
    if (chartData?.length) {
      const lastBar = chartData[chartData.length - 1]!
      const ts = typeof lastBar.time === 'number'
        ? lastBar.time
        : new Date(lastBar.time as string).getTime() / 1000
      setLastBarTime(prev => prev !== ts ? ts : prev)
    }
  }, [chartData])

  // Incremental bar polling (intraday only, 60s interval)
  const { data: latestBars } = useQuery({
    queryKey: ['stock-latest', upperSymbol, simpleInterval],
    queryFn: () => stockApi.getLatestBars(
      upperSymbol,
      simpleInterval,
      lastBarTime,
    ),
    enabled: !!upperSymbol && isIntradayTimeframe && lastBarTime > 0,
    refetchInterval: 60_000,
  })

  // Merge incremental bars into full chart data
  const mergedData = useMemo(() => {
    if (!chartData) return []
    if (!latestBars?.length) return chartData
    const map = new Map(chartData.map(b => [String(b.time), b]))
    for (const bar of latestBars) {
      map.set(String(bar.time), bar)
    }
    return [...map.values()].sort((a, b) =>
      typeof a.time === 'number' && typeof b.time === 'number'
        ? a.time - b.time
        : String(a.time).localeCompare(String(b.time))
    )
  }, [chartData, latestBars])

  // Compute real-time bar from quote for series.update()
  const latestBar = useMemo(() => {
    if (!quote) return null
    if (isIntradayTimeframe) {
      const last = mergedData[mergedData.length - 1]
      return last ? synthesizeIntradayUpdate(quote, last) : null
    }
    const bar = synthesizeTodayBar(quote)
    if (!bar) return null
    // For weekly/monthly charts, the last bar's time is the period end date
    // (e.g., Sunday for weekly, month-end for monthly) which may be ahead of
    // today.  series.update() only accepts the latest time or later, so snap
    // to the last bar's time to avoid "Cannot update oldest data" errors.
    const last = mergedData[mergedData.length - 1]
    if (last && typeof last.time === 'string' && typeof bar.time === 'string' && last.time > bar.time) {
      bar.time = last.time
    }
    return bar
  }, [quote, isIntradayTimeframe, mergedData])

  // Detect non-trading state from quote timestamp staleness
  const isMarketClosed = useMemo(() => {
    if (!quote?.timestamp) return false
    const quoteTime = new Date(quote.timestamp).getTime()
    const ageMinutes = (Date.now() - quoteTime) / 60_000
    return ageMinutes > 5
  }, [quote?.timestamp])

  // Extract futures market closed message
  const chartErrorMessage = (chartError as any)?.response?.status === 409
    ? (chartError as any)?.response?.data?.detail
    : null

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
        title: t('watchlist.addSymbol'),
        description: `${symbol}`,
      })
    },
    onError: (error: unknown, { symbol }) => {
      // Check for 409 Conflict error (stock already in watchlist)
      if (error && typeof error === 'object' && 'response' in error) {
        const axiosError = error as { response?: { status?: number; data?: { detail?: string } } }
        if (axiosError.response?.status === 409) {
          toast({
            title: t('common:status.error', 'Already added'),
            description: `${symbol}`,
            variant: 'default',
          })
          return
        }
      }
      toast({
        title: t('common:status.error', 'Error'),
        description: t('common:errors.unknown', 'Failed to add.'),
        variant: 'destructive',
      })
    },
  })

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
        <h2 className="text-xl font-semibold">{t('common:status.error', 'Failed to load')}</h2>
        <p className="text-muted-foreground">
          {t('search.noResults')} "{upperSymbol}"
        </p>
        <Button onClick={() => refetchQuote()}>
          <RefreshCw className="mr-2 h-4 w-4" />
          {t('common:actions.retry', 'Try again')}
        </Button>
      </div>
    )
  }

  const priceChange = quote?.change ?? 0
  const priceChangePercent = quote?.changePercent ?? 0

  return (
    <StockChatProvider symbol={upperSymbol}>
      <div className="space-y-6">
        {/* Header section - shared between tabs */}
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
              <span className="text-muted-foreground">{t('common:status.loading', 'Loading...')}</span>
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

        {/* Action buttons - shared between tabs */}
        <div className="flex gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">
                <Plus className="mr-2 h-4 w-4" />
                {t('stock.addToWatchlist')}
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
                  {t('watchlist.noWatchlists')}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>

          <Button variant="outline" onClick={() => refetchQuote()}>
            <RefreshCw className="mr-2 h-4 w-4" />
            {t('common:actions.refresh', 'Refresh')}
          </Button>
        </div>

        {/* Primary Tabs: 信息 | AI */}
        <Tabs
          value={primaryTab}
          onValueChange={(v) => setPrimaryTab(v as 'traditional' | 'ai' | 'quant')}
          className="space-y-4"
        >
          <PrimaryTabsList>
            <PrimaryTabsTrigger value="traditional">
              {t('stock.info', '信息')}
            </PrimaryTabsTrigger>
            <PrimaryTabsTrigger value="ai">
              {t('stock.aiTab', 'AI')}
            </PrimaryTabsTrigger>
            <PrimaryTabsTrigger value="quant">
              {t('stock.quantTab', '量化因子')}
            </PrimaryTabsTrigger>
          </PrimaryTabsList>

          {/* 信息 Tab - 完整原始页面布局 */}
          <TabsContent value="traditional" className="space-y-0">
            <div className="grid gap-6 lg:grid-cols-3">
              {/* Left column - Chart and Financials/News */}
              <div className="lg:col-span-2 space-y-4">
                {/* Chart Card — simplified area chart */}
                <Card>
                  <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                    <CardTitle>{t('stock.chart')}</CardTitle>
                    <div className="flex items-center gap-2">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-muted-foreground hover:text-foreground"
                        onClick={() => navigate(`/stock/${upperSymbol}/chart`)}
                        title={t('stock.expandChart')}
                      >
                        <Maximize2 className="h-4 w-4" />
                      </Button>
                      <div className="flex items-center rounded-lg border bg-muted/50 p-0.5">
                        {SIMPLE_TIMEFRAMES.map((tf) => (
                          <Button
                            key={tf}
                            variant="ghost"
                            size="sm"
                            className={cn(
                              'h-7 px-2.5 text-xs font-medium',
                              simpleTimeframe === tf
                                ? 'bg-background text-foreground shadow-sm'
                                : 'text-muted-foreground hover:text-foreground'
                            )}
                            onClick={() => setSimpleTimeframe(tf)}
                          >
                            {tf}
                          </Button>
                        ))}
                      </div>
                    </div>
                  </CardHeader>
                  <CardContent>
                    {isMarketClosed && (
                      <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded">
                        <AlertCircle className="h-3 w-3 shrink-0" />
                        {t('stock.marketClosed')}
                      </div>
                    )}
                    {chartErrorMessage ? (
                      <div className="flex items-center justify-center h-[280px] text-muted-foreground">
                        <p>{chartErrorMessage}</p>
                      </div>
                    ) : (
                      <SimpleChart
                        data={mergedData}
                        latestBar={latestBar}
                        timeframe={simpleTimeframe}
                        symbol={upperSymbol}
                        isLoading={isLoadingChart}
                        height={280}
                      />
                    )}
                  </CardContent>
                </Card>

                {/* Financials / News Sub-tabs */}
                <Tabs defaultValue={isMetalAsset ? 'news' : 'financials'} className="space-y-4">
                  <TabsList className={cn('grid w-full', isMetalAsset ? 'grid-cols-1' : 'grid-cols-2')}>
                    {!isMetalAsset && (
                      <TabsTrigger value="financials">{t('stock.fundamentals')}</TabsTrigger>
                    )}
                    <TabsTrigger value="news">{t('stock.news')}</TabsTrigger>
                  </TabsList>

                  {!isMetalAsset && (
                    <TabsContent value="financials">
                      <Card>
                        <CardHeader>
                          <CardTitle>{t('stock.fundamentals')}</CardTitle>
                          <CardDescription>{t('stock.financials.revenue')}</CardDescription>
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
                              {t('common:status.noData', 'No data available')}
                            </div>
                          )}
                        </CardContent>
                      </Card>
                    </TabsContent>
                  )}

                  <TabsContent value="news">
                    <Card>
                      <CardHeader>
                        <CardTitle>{t('stock.news')}</CardTitle>
                        <CardDescription>{t('news.bySymbol', { symbol: upperSymbol })}</CardDescription>
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
                            {t('news.noNews')}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  </TabsContent>
                </Tabs>
              </div>

              {/* Right column - Sidebar */}
              <div className="space-y-4">
                {/* Key stats */}
                <Card>
                  <CardHeader>
                    <CardTitle>{t('stock.overview')}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {isLoadingQuote ? (
                      <div className="flex h-[200px] items-center justify-center">
                        <Loader2 className="h-6 w-6 animate-spin" />
                      </div>
                    ) : quote ? (
                      isMetalAsset ? (
                        <MetalStatsGrid quote={quote} symbol={upperSymbol} />
                      ) : (
                        <StockStatsGrid quote={quote} />
                      )
                    ) : null}
                  </CardContent>
                </Card>

                {/* Company info / Commodity info */}
                {isMetalAsset ? (
                  <Card>
                    <CardHeader>
                      <CardTitle>{t('stock.commodityInfo', 'Commodity Info')}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <CommodityInfo symbol={upperSymbol} />
                    </CardContent>
                  </Card>
                ) : (
                  <Card>
                    <CardHeader>
                      <CardTitle>{t('stock.description')}</CardTitle>
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
                          {t('common:status.noData', 'No data available')}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>
            </div>
          </TabsContent>

          {/* AI Tab - 全宽单列布局 */}
          <TabsContent value="ai">
            <AITab
              symbol={upperSymbol}
              subTab={subTab as AISubTab}
              onSubTabChange={(tab) => setSubTab(tab)}
            />
          </TabsContent>

          {/* Quant Tab - 量化因子 */}
          <TabsContent value="quant">
            <div className="grid gap-6 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <QlibFactorPanel
                  symbol={upperSymbol}
                  market={quote?.market ?? 'US'}
                />
              </div>
              <div className="space-y-4">
                {/* Key stats sidebar - reuse from traditional tab */}
                <Card>
                  <CardHeader>
                    <CardTitle>{t('stock.overview')}</CardTitle>
                  </CardHeader>
                  <CardContent>
                    {isLoadingQuote ? (
                      <div className="flex h-[200px] items-center justify-center">
                        <Loader2 className="h-6 w-6 animate-spin" />
                      </div>
                    ) : quote ? (
                      isMetalAsset ? (
                        <MetalStatsGrid quote={quote} symbol={upperSymbol} />
                      ) : (
                        <StockStatsGrid quote={quote} />
                      )
                    ) : null}
                  </CardContent>
                </Card>
              </div>
            </div>
          </TabsContent>
        </Tabs>

        {/* Floating AI Chat Widget - always visible */}
        <StockChatWidget symbol={upperSymbol} />
      </div>
    </StockChatProvider>
  )
}
