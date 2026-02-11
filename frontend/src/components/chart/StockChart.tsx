import { useEffect, useRef, useCallback, useState } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData as LWCandlestickData,
  type HistogramData,
  type Time,
  ColorType,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts'
import { useTranslation } from 'react-i18next'
import { useThemeStore } from '@/stores/themeStore'
import type { CandlestickData, ChartTimeframe, SentimentTimelineItem } from '@/types'
import { cn, isMetal } from '@/lib/utils'

interface StockChartProps {
  data: CandlestickData[]
  timeframe: ChartTimeframe
  symbol: string
  isLoading?: boolean
  className?: string
  height?: number
  onTimeframeChange?: (tf: ChartTimeframe) => void
  sentimentData?: SentimentTimelineItem[] | undefined
  showVolume?: boolean
}

interface CrosshairData {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number | undefined
  sentiment: number | undefined
}

// Ordered timeframes for zoom navigation
const TIMEFRAME_ORDER: ChartTimeframe[] = ['1H', '1D', '1W', '1M', '3M', '6M', '1Y', '5Y', 'ALL']

// How many bars each timeframe is expected to show (approximate)
// Used to decide when to switch: if visible bars drop below zoomInAt → go lower,
// if total data bars are all visible and user keeps zooming out → go higher
const TIMEFRAME_BAR_COUNTS: Record<ChartTimeframe, { expected: number; zoomInAt: number; zoomOutAt: number }> = {
  '1H':  { expected: 60,  zoomInAt: 0,  zoomOutAt: 60  },  // 1m bars in 1 hour
  '1D':  { expected: 78,  zoomInAt: 15, zoomOutAt: 78  },  // 5m bars in a day
  '1W':  { expected: 130, zoomInAt: 20, zoomOutAt: 130 },  // 15m bars in 5 days
  '1M':  { expected: 22,  zoomInAt: 5,  zoomOutAt: 22  },  // daily bars in 1mo
  '3M':  { expected: 65,  zoomInAt: 15, zoomOutAt: 65  },  // daily bars in 3mo
  '6M':  { expected: 130, zoomInAt: 40, zoomOutAt: 130 },  // daily bars in 6mo
  '1Y':  { expected: 252, zoomInAt: 80, zoomOutAt: 252 },  // daily bars in 1y
  '5Y':  { expected: 260, zoomInAt: 50, zoomOutAt: 260 },  // weekly bars in 5y
  'ALL': { expected: 999, zoomInAt: 50, zoomOutAt: 999 },  // monthly bars
}

// Theme configurations for the chart
const lightTheme = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#374151',
  },
  grid: {
    vertLines: { color: '#e5e7eb' },
    horzLines: { color: '#e5e7eb' },
  },
  crosshair: {
    vertLine: {
      color: '#6b7280',
      labelBackgroundColor: '#374151',
    },
    horzLine: {
      color: '#6b7280',
      labelBackgroundColor: '#374151',
    },
  },
  rightPriceScale: {
    borderColor: '#e5e7eb',
  },
  timeScale: {
    borderColor: '#e5e7eb',
  },
}

const darkTheme = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#d1d5db',
  },
  grid: {
    vertLines: { color: '#374151' },
    horzLines: { color: '#374151' },
  },
  crosshair: {
    vertLine: {
      color: '#9ca3af',
      labelBackgroundColor: '#1f2937',
    },
    horzLine: {
      color: '#9ca3af',
      labelBackgroundColor: '#1f2937',
    },
  },
  rightPriceScale: {
    borderColor: '#374151',
  },
  timeScale: {
    borderColor: '#374151',
  },
}

// Convert our data format to lightweight-charts format
function convertToChartData(data: CandlestickData[]): LWCandlestickData<Time>[] {
  if (!Array.isArray(data)) return []
  return data
    .filter((item) => item.open != null && item.high != null && item.low != null && item.close != null)
    .map((item) => ({
      time: item.time as Time,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }))
}

function convertToVolumeData(data: CandlestickData[]): HistogramData<Time>[] {
  if (!Array.isArray(data)) return []
  return data
    .filter((item) => item.volume !== undefined)
    .map((item) => ({
      time: item.time as Time,
      value: item.volume ?? 0,
      color: item.close >= item.open ? 'rgba(34, 197, 94, 0.5)' : 'rgba(239, 68, 68, 0.5)',
    }))
}

export default function StockChart({
  data,
  timeframe,
  symbol,
  isLoading = false,
  className,
  height = 400,
  onTimeframeChange,
  sentimentData,
  showVolume = true,
}: StockChartProps) {
  const { t } = useTranslation('dashboard')
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const sentimentSeriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const { resolvedTheme } = useThemeStore()
  const [crosshairData, setCrosshairData] = useState<CrosshairData | null>(null)

  // Refs for zoom-switch logic (avoid stale closures)
  const timeframeRef = useRef(timeframe)
  const dataLenRef = useRef(data?.length ?? 0)
  const switchCooldownRef = useRef(false)
  const onTimeframeChangeRef = useRef(onTimeframeChange)

  useEffect(() => {
    timeframeRef.current = timeframe
    // Set cooldown to prevent auto-switch from overriding manual timeframe change
    switchCooldownRef.current = true
    setTimeout(() => { switchCooldownRef.current = false }, 1500)
  }, [timeframe])
  useEffect(() => { dataLenRef.current = data?.length ?? 0 }, [data])
  useEffect(() => { onTimeframeChangeRef.current = onTimeframeChange }, [onTimeframeChange])

  // Format price with appropriate decimal places
  const formatPrice = useCallback((price: number): string => {
    if (price >= 1000) {
      return price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    }
    return price.toFixed(4)
  }, [])

  // Format volume with compact notation
  // Note: For precious metals (futures), volume represents contracts, not shares
  const formatVolume = useCallback((volume: number): string => {
    if (volume >= 1_000_000_000) {
      return `${(volume / 1_000_000_000).toFixed(2)}B`
    }
    if (volume >= 1_000_000) {
      return `${(volume / 1_000_000).toFixed(2)}M`
    }
    if (volume >= 1_000) {
      return `${(volume / 1_000).toFixed(2)}K`
    }
    return volume.toString()
  }, [])

  // Check if this is a metal symbol for volume label
  const isMetalSymbol = isMetal(symbol)

  // Initialize chart
  useEffect(() => {
    if (!chartContainerRef.current) return

    const theme = resolvedTheme === 'dark' ? darkTheme : lightTheme

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height: height,
      layout: {
        ...theme.layout,
        attributionLogo: false,
      },
      grid: theme.grid,
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: {
          ...theme.crosshair.vertLine,
          width: 1,
          style: LineStyle.Solid,
        },
        horzLine: {
          ...theme.crosshair.horzLine,
          width: 1,
          style: LineStyle.Solid,
        },
      },
      rightPriceScale: {
        borderColor: theme.rightPriceScale.borderColor,
        scaleMargins: {
          top: 0.1,
          bottom: 0.3,
        },
      },
      timeScale: {
        borderColor: theme.timeScale.borderColor,
        timeVisible: true,
        secondsVisible: false,
      },
      handleScroll: {
        vertTouchDrag: false,
      },
    })

    // Create candlestick series
    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#22c55e',
      borderDownColor: '#ef4444',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    })

    // Create volume series
    const volumeSeries = chart.addHistogramSeries({
      color: '#26a69a',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: '',
    })

    // Configure volume series scale
    volumeSeries.priceScale().applyOptions({
      scaleMargins: {
        top: 0.8,
        bottom: 0,
      },
    })

    // Create sentiment baseline series (overlaid on main chart area)
    const sentimentSeries = chart.addBaselineSeries({
      priceScaleId: 'sentiment',
      baseValue: { type: 'price', price: 0 },
      topLineColor: 'rgba(34, 197, 94, 1)',
      topFillColor1: 'rgba(34, 197, 94, 0.28)',
      topFillColor2: 'rgba(34, 197, 94, 0.05)',
      bottomLineColor: 'rgba(239, 68, 68, 1)',
      bottomFillColor1: 'rgba(239, 68, 68, 0.05)',
      bottomFillColor2: 'rgba(239, 68, 68, 0.28)',
      lineWidth: 2,
      priceFormat: {
        type: 'custom',
        formatter: (v: number) => v.toFixed(2),
      },
    })
    sentimentSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.1, bottom: 0.3 },
      visible: false, // Hidden by default; toggled on when data loads
    })
    sentimentSeries.applyOptions({ visible: false })

    chartRef.current = chart
    candlestickSeriesRef.current = candlestickSeries
    volumeSeriesRef.current = volumeSeries
    sentimentSeriesRef.current = sentimentSeries

    // Handle crosshair move for tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData || param.seriesData.size === 0) {
        setCrosshairData(null)
        return
      }

      const candleData = param.seriesData.get(candlestickSeries) as LWCandlestickData<Time> | undefined
      const volumeData = param.seriesData.get(volumeSeries) as HistogramData<Time> | undefined
      const sentData = param.seriesData.get(sentimentSeries) as { value?: number } | undefined

      if (candleData) {
        setCrosshairData({
          time: param.time as string | number,
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          volume: volumeData?.value,
          sentiment: sentData?.value,
        })
      }
    })

    // Auto-switch timeframe on zoom
    chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (!range || !onTimeframeChangeRef.current || switchCooldownRef.current) return

      const visibleBars = range.to - range.from
      const tf = timeframeRef.current
      const tfIdx = TIMEFRAME_ORDER.indexOf(tf)
      const config = TIMEFRAME_BAR_COUNTS[tf]
      const totalBars = dataLenRef.current

      // Zoom in: visible bars dropped well below threshold → switch to lower timeframe
      if (tfIdx > 0 && config.zoomInAt > 0 && visibleBars < config.zoomInAt) {
        switchCooldownRef.current = true
        setTimeout(() => { switchCooldownRef.current = false }, 800)
        onTimeframeChangeRef.current(TIMEFRAME_ORDER[tfIdx - 1]!)
        return
      }

      // Zoom out: all bars visible and user is zoomed beyond → switch to higher timeframe
      if (tfIdx < TIMEFRAME_ORDER.length - 1 && totalBars > 0) {
        // range.from can go negative when zoomed out beyond data
        if (range.from < -totalBars * 0.3 || visibleBars > totalBars * 1.5) {
          switchCooldownRef.current = true
          setTimeout(() => { switchCooldownRef.current = false }, 800)
          onTimeframeChangeRef.current(TIMEFRAME_ORDER[tfIdx + 1]!)
          return
        }
      }
    })

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        })
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(chartContainerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      candlestickSeriesRef.current = null
      volumeSeriesRef.current = null
      sentimentSeriesRef.current = null
    }
  }, [height, resolvedTheme])

  // Update data when it changes
  useEffect(() => {
    if (!candlestickSeriesRef.current || !volumeSeriesRef.current || !Array.isArray(data) || data.length === 0) return

    const candleData = convertToChartData(data)
    const volumeData = convertToVolumeData(data)

    candlestickSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volumeData)

    // Fit content to view
    if (chartRef.current) {
      chartRef.current.timeScale().fitContent()
    }
  }, [data])

  // Update sentiment data when it changes
  // Sentiment data uses YYYY-MM-DD strings (daily aggregation), which is incompatible
  // with intraday timeframes that use Unix timestamps. Hide sentiment on intraday charts.
  const isIntradayTimeframe = timeframe === '1H' || timeframe === '1D' || timeframe === '1W'
  useEffect(() => {
    if (!sentimentSeriesRef.current) return
    const hasData = !isIntradayTimeframe && !!sentimentData?.length
    // Toggle series and price scale visibility
    sentimentSeriesRef.current.applyOptions({ visible: hasData })
    sentimentSeriesRef.current.priceScale().applyOptions({ visible: hasData })
    if (!hasData) {
      sentimentSeriesRef.current.setData([])
      return
    }
    sentimentSeriesRef.current.setData(
      sentimentData!.map((d) => ({
        time: d.date as Time,
        value: d.score,
      }))
    )
  }, [sentimentData, isIntradayTimeframe])

  // Toggle volume visibility
  useEffect(() => {
    if (!volumeSeriesRef.current) return
    volumeSeriesRef.current.applyOptions({ visible: showVolume })
    volumeSeriesRef.current.priceScale().applyOptions({ visible: showVolume })
  }, [showVolume])

  // Update theme when it changes
  useEffect(() => {
    if (!chartRef.current) return

    const theme = resolvedTheme === 'dark' ? darkTheme : lightTheme

    chartRef.current.applyOptions({
      layout: theme.layout,
      grid: theme.grid,
      rightPriceScale: {
        borderColor: theme.rightPriceScale.borderColor,
      },
      timeScale: {
        borderColor: theme.timeScale.borderColor,
      },
    })
  }, [resolvedTheme])

  // Get the last data point for comparison
  const lastDataPoint = Array.isArray(data) && data.length > 0 ? data[data.length - 1] : null
  const displayData = crosshairData ?? (lastDataPoint ? {
    time: lastDataPoint.time,
    open: lastDataPoint.open,
    high: lastDataPoint.high,
    low: lastDataPoint.low,
    close: lastDataPoint.close,
    volume: lastDataPoint.volume,
    sentiment: undefined,
  } : null)

  const priceChange = displayData ? displayData.close - displayData.open : 0
  const priceChangePercent = displayData && displayData.open !== 0
    ? ((displayData.close - displayData.open) / displayData.open) * 100
    : 0

  return (
    <div className={cn('relative', className)}>
      {/* Crosshair data display */}
      <div className="absolute left-4 top-2 z-10 rounded-lg bg-background/90 p-3 text-sm shadow-sm backdrop-blur-sm">
        <div className="mb-1 font-semibold">{symbol}</div>
        {displayData ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{t('stock.ohlc.open')}:</span>
              <span>{formatPrice(displayData.open)}</span>
              <span className="text-muted-foreground">{t('stock.ohlc.high')}:</span>
              <span>{formatPrice(displayData.high)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{t('stock.ohlc.low')}:</span>
              <span>{formatPrice(displayData.low)}</span>
              <span className="text-muted-foreground">{t('stock.ohlc.close')}:</span>
              <span
                className={cn(
                  'font-medium',
                  priceChange >= 0 ? 'text-stock-up' : 'text-stock-down'
                )}
              >
                {formatPrice(displayData.close)}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  'text-xs',
                  priceChange >= 0 ? 'text-stock-up' : 'text-stock-down'
                )}
              >
                {priceChange >= 0 ? '+' : ''}{formatPrice(priceChange)} ({priceChangePercent >= 0 ? '+' : ''}{priceChangePercent.toFixed(2)}%)
              </span>
              {displayData.volume !== undefined && (
                <>
                  <span className="text-muted-foreground">
                    {isMetalSymbol ? `${t('stock.contracts')}:` : `${t('stock.vol')}:`}
                  </span>
                  <span>{formatVolume(displayData.volume)}</span>
                </>
              )}
              {displayData.sentiment !== undefined && (
                <>
                  <span className="text-muted-foreground">{t('stock.sent')}:</span>
                  <span
                    className={cn(
                      'font-medium',
                      displayData.sentiment > 0
                        ? 'text-stock-up'
                        : displayData.sentiment < 0
                          ? 'text-stock-down'
                          : 'text-muted-foreground'
                    )}
                  >
                    {displayData.sentiment >= 0 ? '+' : ''}
                    {displayData.sentiment.toFixed(2)}
                  </span>
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="text-muted-foreground">{t('stock.noData')}</div>
        )}
      </div>

      {/* Loading overlay */}
      {isLoading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-background/50 backdrop-blur-sm">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      )}

      {/* Chart container */}
      <div
        ref={chartContainerRef}
        className="w-full"
        style={{ height: `${height}px` }}
      />

      {/* Empty state */}
      {!isLoading && (!Array.isArray(data) || data.length === 0) && (
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="text-center text-muted-foreground">
            <p>{t('stock.noChartData')}</p>
            <p className="text-sm">{t('stock.tryDifferentPeriod')}</p>
          </div>
        </div>
      )}
    </div>
  )
}
