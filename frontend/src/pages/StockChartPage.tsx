import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { useEffect, useCallback, useMemo, useState } from 'react'
import {
  TrendingUp,
  TrendingDown,
  ArrowLeft,
  Loader2,
  AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { StockChart, ChartControls, useChartControls } from '@/components/chart'
import type { ChartInterval } from '@/components/chart'
import { TIMEFRAME_DEFAULT_INTERVAL } from '@/api'
import {
  cn,
  formatCurrency,
  formatPercent,
  getPriceChangeColor,
} from '@/lib/utils'
import { stockApi, newsApi, synthesizeTodayBar, synthesizeIntradayUpdate } from '@/api'

export default function StockChartPage() {
  const { t } = useTranslation('dashboard')
  const { symbol } = useParams<{ symbol: string }>()
  const navigate = useNavigate()
  const chartControls = useChartControls('1D', '1m')
  const [lastBarTime, setLastBarTime] = useState<number>(0)
  const [chartHeight, setChartHeight] = useState(500)

  const upperSymbol = symbol?.toUpperCase() ?? ''

  // Responsive chart height
  useEffect(() => {
    const updateHeight = () => {
      // viewport - header(64) - controls(~100) - padding(~36)
      setChartHeight(Math.max(400, Math.min(window.innerHeight - 200, 700)))
    }
    updateHeight()
    window.addEventListener('resize', updateHeight)
    return () => window.removeEventListener('resize', updateHeight)
  }, [])

  // Fetch quote for header price display + real-time bar synthesis
  const { data: quote, isLoading: isLoadingQuote } = useQuery({
    queryKey: ['stock-quote', upperSymbol],
    queryFn: () => stockApi.getQuote(upperSymbol),
    enabled: !!upperSymbol,
    refetchInterval: 30_000,
  })

  // Fetch info for company name
  const { data: info } = useQuery({
    queryKey: ['stock-info', upperSymbol],
    queryFn: () => stockApi.getInfo(upperSymbol),
    enabled: !!upperSymbol,
  })

  // Intraday detection
  const isIntradayTimeframe = chartControls.timeframe === '1H' || chartControls.timeframe === '1D' || chartControls.timeframe === '1W'

  // Chart history data
  const {
    data: chartData,
    isLoading: isLoadingChart,
    error: chartError,
  } = useQuery({
    queryKey: ['stock-history', upperSymbol, chartControls.timeframe, chartControls.interval, chartControls.visibleRange],
    queryFn: () => {
      if (chartControls.visibleRange) {
        return stockApi.getHistory(upperSymbol, chartControls.timeframe, {
          intervalOverride: chartControls.interval,
          start: chartControls.visibleRange.start,
          end: chartControls.visibleRange.end,
        })
      }
      return stockApi.getHistory(upperSymbol, chartControls.timeframe, {
        intervalOverride: chartControls.interval,
      })
    },
    enabled: !!upperSymbol,
    placeholderData: keepPreviousData,
    staleTime: isIntradayTimeframe ? 60_000 : 5 * 60_000,
    retry: (failureCount, error: any) => {
      if (error?.response?.status === 409) return false
      return failureCount < 2
    },
  })

  // Track last bar time for incremental fetching
  useEffect(() => {
    if (chartData?.length) {
      const lastBar = chartData[chartData.length - 1]!
      const ts = typeof lastBar.time === 'number'
        ? lastBar.time
        : new Date(lastBar.time as string).getTime() / 1000
      setLastBarTime(prev => prev !== ts ? ts : prev)
    }
  }, [chartData])

  // Incremental bar polling (intraday only)
  const { data: latestBars } = useQuery({
    queryKey: ['stock-latest', upperSymbol, chartControls.interval],
    queryFn: () => stockApi.getLatestBars(upperSymbol, chartControls.interval, lastBarTime),
    enabled: !!upperSymbol && isIntradayTimeframe && lastBarTime > 0 && !chartControls.visibleRange,
    refetchInterval: 60_000,
  })

  // Merge incremental bars
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

  // Real-time bar from quote
  const latestBar = useMemo(() => {
    if (!quote) return null
    if (isIntradayTimeframe) {
      const last = mergedData[mergedData.length - 1]
      return last ? synthesizeIntradayUpdate(quote, last) : null
    }
    const bar = synthesizeTodayBar(quote)
    if (!bar) return null
    const last = mergedData[mergedData.length - 1]
    if (last && typeof last.time === 'string' && typeof bar.time === 'string' && last.time > bar.time) {
      bar.time = last.time
    }
    return bar
  }, [quote, isIntradayTimeframe, mergedData])

  // Market closed detection
  const isMarketClosed = useMemo(() => {
    if (!quote?.timestamp) return false
    const quoteTime = new Date(quote.timestamp).getTime()
    const ageMinutes = (Date.now() - quoteTime) / 60_000
    return ageMinutes > 5
  }, [quote?.timestamp])

  // Futures market closed error
  const chartErrorMessage = (chartError as any)?.response?.status === 409
    ? (chartError as any)?.response?.data?.detail
    : null

  // Indicator visibility
  const showVolume = chartControls.activeIndicators.includes('VOL')
  const showSentiment = chartControls.activeIndicators.includes('SENT')

  const { data: sentimentData } = useQuery({
    queryKey: ['sentiment-timeline', upperSymbol],
    queryFn: () => newsApi.getSentimentTimeline(upperSymbol, 90),
    enabled: !!upperSymbol && showSentiment,
    staleTime: 5 * 60 * 1000,
  })

  // Technical indicators
  const showMA = chartControls.activeIndicators.includes('MA')
  const showRSI = chartControls.activeIndicators.includes('RSI')
  const showMACD = chartControls.activeIndicators.includes('MACD')
  const showBB = chartControls.activeIndicators.includes('BB')
  const hasAnyTechnicalIndicator = showMA || showRSI || showMACD || showBB

  const indicatorTypes = [
    ...(showMA ? ['sma'] : []),
    ...(showRSI ? ['rsi'] : []),
    ...(showMACD ? ['macd'] : []),
    ...(showBB ? ['bb'] : []),
  ]

  const { data: indicatorData } = useQuery({
    queryKey: ['stock-indicators', upperSymbol, chartControls.timeframe, chartControls.interval, chartControls.visibleRange, indicatorTypes.join(','), chartControls.maPeriods.join(',')],
    queryFn: () => {
      const opts: {
        maPeriods: number[]
        intervalOverride?: string
        start?: string
        end?: string
      } = {
        maPeriods: chartControls.maPeriods,
        intervalOverride: chartControls.interval,
      }
      if (chartControls.visibleRange) {
        opts.start = chartControls.visibleRange.start
        opts.end = chartControls.visibleRange.end
      }
      return stockApi.getIndicators(upperSymbol, chartControls.timeframe, indicatorTypes, opts)
    },
    enabled: !!upperSymbol && hasAnyTechnicalIndicator,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })

  // Zoom handlers
  const handleVisibleRangeChange = useCallback((newInterval: string, range: { start: string; end: string }) => {
    chartControls.setInterval(newInterval as ChartInterval)
    chartControls.setVisibleRange(range)
  }, [chartControls])

  const handleVisibleRangeReset = useCallback(() => {
    chartControls.setInterval(TIMEFRAME_DEFAULT_INTERVAL[chartControls.timeframe] as ChartInterval)
    chartControls.setVisibleRange(null)
  }, [chartControls])

  const priceChange = quote?.change ?? 0
  const priceChangePercent = quote?.changePercent ?? 0

  return (
    <div className="space-y-3">
      {/* Compact header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => navigate(`/stock/${upperSymbol}`)}
            className="h-8 w-8"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold">{upperSymbol}</h1>
            <span className="text-sm text-muted-foreground">
              {info?.name ?? quote?.name ?? ''}
            </span>
          </div>
        </div>

        {/* Price display */}
        {isLoadingQuote ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        ) : quote ? (
          <div className="flex items-center gap-3">
            <span className="text-xl font-bold">{formatCurrency(quote.price)}</span>
            <span
              className={cn(
                'flex items-center gap-1 text-sm font-medium',
                getPriceChangeColor(priceChange)
              )}
            >
              {priceChange >= 0 ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
              {priceChange >= 0 ? '+' : ''}{formatCurrency(Math.abs(priceChange))} ({formatPercent(priceChangePercent)})
            </span>
          </div>
        ) : null}
      </div>

      {/* Controls: timeframe + interval dropdown + indicator dropdown */}
      <ChartControls
        timeframe={chartControls.timeframe}
        onTimeframeChange={chartControls.setTimeframe}
        interval={chartControls.interval}
        onIntervalChange={chartControls.setInterval}
        indicators={chartControls.activeIndicators}
        onIndicatorToggle={chartControls.toggleIndicator}
        maPeriods={chartControls.maPeriods}
      />

      {/* Market closed banner */}
      {isMarketClosed && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded">
          <AlertCircle className="h-3 w-3 shrink-0" />
          {t('stock.marketClosed')}
        </div>
      )}

      {/* Full chart */}
      {chartErrorMessage ? (
        <div className="flex items-center justify-center text-muted-foreground" style={{ height: chartHeight }}>
          <p>{chartErrorMessage}</p>
        </div>
      ) : (
        <StockChart
          data={mergedData}
          latestBar={latestBar}
          timeframe={chartControls.timeframe}
          symbol={upperSymbol}
          isLoading={isLoadingChart}
          height={chartHeight}
          onTimeframeChange={chartControls.setTimeframe}
          showVolume={showVolume}
          sentimentData={showSentiment ? sentimentData?.data : undefined}
          indicatorData={hasAnyTechnicalIndicator && indicatorData?.symbol === upperSymbol ? indicatorData : undefined}
          activeIndicators={chartControls.activeIndicators}
          interval={chartControls.interval}
          onVisibleRangeChange={handleVisibleRangeChange}
          onVisibleRangeReset={handleVisibleRangeReset}
          isZoomMode={!!chartControls.visibleRange}
        />
      )}
    </div>
  )
}
