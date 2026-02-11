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
import type { CandlestickData, ChartTimeframe, SentimentTimelineItem, TechnicalIndicatorsData } from '@/types'
import type { ChartIndicator } from './ChartControls'
import { resolveInterval } from './ChartControls'
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
  indicatorData?: TechnicalIndicatorsData | undefined
  activeIndicators?: ChartIndicator[] | undefined
  /** Current data interval (e.g. '5m', '1d') for zoom-change detection */
  interval?: string
  /** Called when zooming in triggers a new interval */
  onVisibleRangeChange?: (interval: string, visibleRange: { start: string; end: string }) => void
  /** Called when user zooms back out to nearly full range */
  onVisibleRangeReset?: () => void
  /** When true, skip fitContent() on data updates (prevents zoom reset feedback loop) */
  isZoomMode?: boolean
}

interface CrosshairData {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number | undefined
  sentiment: number | undefined
  rsi: number | undefined
  macdValue: number | undefined
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

// Indicator color constants
const MA_COLORS = ['#2962FF', '#FF6D00', '#AA00FF', '#00BFA5', '#FFD600']
const RSI_COLOR = '#E040FB'
const RSI_OVERBOUGHT = 70
const RSI_OVERSOLD = 30
const MACD_LINE_COLOR = '#2196F3'
const MACD_SIGNAL_COLOR = '#FF9800'
const POSITIVE_HIST_COLOR = 'rgba(34, 197, 94, 0.7)'
const NEGATIVE_HIST_COLOR = 'rgba(239, 68, 68, 0.7)'
const BB_COLOR = 'rgba(103, 58, 183, 0.7)'
const BB_BAND_COLOR = 'rgba(103, 58, 183, 0.4)'

export default function StockChart({
  data,
  timeframe,
  symbol,
  isLoading = false,
  className,
  height = 400,
  onTimeframeChange: _onTimeframeChange,
  sentimentData,
  showVolume = true,
  indicatorData,
  activeIndicators,
  interval: _interval,
  onVisibleRangeChange: _onVisibleRangeChange,
  onVisibleRangeReset: _onVisibleRangeReset,
  isZoomMode = false,
}: StockChartProps) {
  const { t } = useTranslation('dashboard')
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candlestickSeriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeSeriesRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const sentimentSeriesRef = useRef<ISeriesApi<'Baseline'> | null>(null)
  const maSeriesRefs = useRef<Map<string, ISeriesApi<'Line'>>>(new Map())
  const rsiSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdLineRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdSignalRef = useRef<ISeriesApi<'Line'> | null>(null)
  const macdHistRef = useRef<ISeriesApi<'Histogram'> | null>(null)
  const bbUpperRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bbMiddleRef = useRef<ISeriesApi<'Line'> | null>(null)
  const bbLowerRef = useRef<ISeriesApi<'Line'> | null>(null)
  const { resolvedTheme } = useThemeStore()
  const [crosshairData, setCrosshairData] = useState<CrosshairData | null>(null)

  const zoomDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

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

    // RSI series
    const rsiSeries = chart.addLineSeries({
      color: RSI_COLOR,
      lineWidth: 1,
      priceScaleId: 'rsi',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    rsiSeries.createPriceLine({ price: RSI_OVERBOUGHT, color: 'rgba(239, 68, 68, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    rsiSeries.createPriceLine({ price: RSI_OVERSOLD, color: 'rgba(34, 197, 94, 0.4)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    rsiSeries.applyOptions({ visible: false })
    rsiSeriesRef.current = rsiSeries

    // MACD series
    const macdLine = chart.addLineSeries({
      color: MACD_LINE_COLOR,
      lineWidth: 1,
      priceScaleId: 'macd',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    macdLine.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
      visible: false,
    })
    macdLine.applyOptions({ visible: false })
    macdLineRef.current = macdLine

    const macdSignal = chart.addLineSeries({
      color: MACD_SIGNAL_COLOR,
      lineWidth: 1,
      priceScaleId: 'macd',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    macdSignal.applyOptions({ visible: false })
    macdSignalRef.current = macdSignal

    const macdHist = chart.addHistogramSeries({
      priceScaleId: 'macd',
      lastValueVisible: false,
      priceLineVisible: false,
    })
    macdHist.applyOptions({ visible: false })
    macdHistRef.current = macdHist

    // Bollinger Bands
    const bbUpper = chart.addLineSeries({
      color: BB_BAND_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbUpper.applyOptions({ visible: false })
    bbUpperRef.current = bbUpper

    const bbMiddle = chart.addLineSeries({
      color: BB_COLOR,
      lineWidth: 1,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbMiddle.applyOptions({ visible: false })
    bbMiddleRef.current = bbMiddle

    const bbLower = chart.addLineSeries({
      color: BB_BAND_COLOR,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbLower.applyOptions({ visible: false })
    bbLowerRef.current = bbLower

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
      const rsiData = rsiSeriesRef.current ? param.seriesData.get(rsiSeriesRef.current) as { value?: number } | undefined : undefined
      const macdData = macdLineRef.current ? param.seriesData.get(macdLineRef.current) as { value?: number } | undefined : undefined

      if (candleData) {
        setCrosshairData({
          time: param.time as string | number,
          open: candleData.open,
          high: candleData.high,
          low: candleData.low,
          close: candleData.close,
          volume: volumeData?.value,
          sentiment: sentData?.value,
          rsi: rsiData?.value,
          macdValue: macdData?.value,
        })
      }
    })

    // NOTE: Zoom-triggered auto-switch disabled for now (too many edge cases
    // with cascading switches and stale data). Manual interval dropdown works.
    void resolveInterval // suppress unused import warning

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        })
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    const container = chartContainerRef.current
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      if (zoomDebounceRef.current) clearTimeout(zoomDebounceRef.current)
      maSeriesRefs.current.forEach((s) => { try { chart.removeSeries(s) } catch { /* already removed */ } })
      maSeriesRefs.current.clear()
      chart.remove()
      chartRef.current = null
      candlestickSeriesRef.current = null
      volumeSeriesRef.current = null
      sentimentSeriesRef.current = null
      rsiSeriesRef.current = null
      macdLineRef.current = null
      macdSignalRef.current = null
      macdHistRef.current = null
      bbUpperRef.current = null
      bbMiddleRef.current = null
      bbLowerRef.current = null
    }
  }, [height, resolvedTheme])

  // Update data when it changes
  useEffect(() => {
    if (!candlestickSeriesRef.current || !volumeSeriesRef.current || !Array.isArray(data) || data.length === 0) return

    const candleData = convertToChartData(data)
    const volumeData = convertToVolumeData(data)

    candlestickSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volumeData)

    // Fit content to view (skip in zoom mode to prevent feedback loop)
    if (chartRef.current && !isZoomMode) {
      chartRef.current.timeScale().fitContent()
    }
  }, [data, isZoomMode])

  // Update sentiment data when it changes
  // Sentiment data uses YYYY-MM-DD strings (daily aggregation), which is incompatible
  // with intraday timeframes that use Unix timestamps. Hide sentiment on intraday charts.
  const isIntradayTimeframe = timeframe === '1H' || timeframe === '1D' || timeframe === '1W'
  useEffect(() => {
    if (!sentimentSeriesRef.current) return
    const showSent = !!activeIndicators?.includes('SENT')
    const hasData = showSent && !isIntradayTimeframe && !!sentimentData?.length
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
  }, [sentimentData, isIntradayTimeframe, activeIndicators])

  // Toggle volume visibility
  useEffect(() => {
    if (!volumeSeriesRef.current) return
    volumeSeriesRef.current.applyOptions({ visible: showVolume })
    volumeSeriesRef.current.priceScale().applyOptions({ visible: showVolume })
  }, [showVolume])

  // Update indicator series when data changes
  useEffect(() => {
    const chart = chartRef.current
    if (!chart) return

    const showMA = !!activeIndicators?.includes('MA')
    const showRSI = !!activeIndicators?.includes('RSI')
    const showMACD = !!activeIndicators?.includes('MACD')
    const showBB = !!activeIndicators?.includes('BB')

    // Helper to convert indicator time format to chart time format
    const toChartTime = (point: { time: string | number }) => point.time as Time

    // --- MA Lines ---
    // Remove old MA series
    maSeriesRefs.current.forEach((s) => {
      try { chart.removeSeries(s) } catch { /* already removed */ }
    })
    maSeriesRefs.current.clear()

    if (showMA && indicatorData?.ma) {
      const maKeys = Object.keys(indicatorData.ma)
      maKeys.forEach((key, idx) => {
        const maData = indicatorData.ma![key]
        if (!maData?.series?.length) return
        const color = MA_COLORS[idx % MA_COLORS.length]!
        const series = chart.addLineSeries({
          color,
          lineWidth: 1,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          title: key.toUpperCase().replace('_', ' '),
        })
        series.setData(maData.series.map(p => ({ time: toChartTime(p), value: p.value })))
        maSeriesRefs.current.set(key, series)
      })
    }

    // --- RSI ---
    if (rsiSeriesRef.current) {
      const hasRSI = showRSI && !!indicatorData?.rsi?.series?.length
      rsiSeriesRef.current.applyOptions({ visible: hasRSI })
      rsiSeriesRef.current.priceScale().applyOptions({ visible: hasRSI })
      if (hasRSI) {
        rsiSeriesRef.current.setData(
          indicatorData!.rsi!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        rsiSeriesRef.current.setData([])
      }
    }

    // --- MACD ---
    const hasMACD = showMACD && !!indicatorData?.macd?.macdLine?.length
    if (macdLineRef.current) {
      macdLineRef.current.applyOptions({ visible: hasMACD })
      macdLineRef.current.priceScale().applyOptions({ visible: hasMACD })
      if (hasMACD) {
        macdLineRef.current.setData(
          indicatorData!.macd!.macdLine.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        macdLineRef.current.setData([])
      }
    }
    if (macdSignalRef.current) {
      macdSignalRef.current.applyOptions({ visible: hasMACD })
      if (hasMACD) {
        macdSignalRef.current.setData(
          indicatorData!.macd!.signalLine.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        macdSignalRef.current.setData([])
      }
    }
    if (macdHistRef.current) {
      macdHistRef.current.applyOptions({ visible: hasMACD })
      if (hasMACD) {
        macdHistRef.current.setData(
          indicatorData!.macd!.histogram.map(p => ({
            time: toChartTime(p),
            value: p.value,
            color: p.value >= 0 ? POSITIVE_HIST_COLOR : NEGATIVE_HIST_COLOR,
          }))
        )
      } else {
        macdHistRef.current.setData([])
      }
    }

    // --- Bollinger Bands ---
    const hasBB = showBB && !!indicatorData?.bb?.upper?.length
    if (bbUpperRef.current) {
      bbUpperRef.current.applyOptions({ visible: hasBB })
      if (hasBB) {
        bbUpperRef.current.setData(
          indicatorData!.bb!.upper.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        bbUpperRef.current.setData([])
      }
    }
    if (bbMiddleRef.current) {
      bbMiddleRef.current.applyOptions({ visible: hasBB })
      if (hasBB) {
        bbMiddleRef.current.setData(
          indicatorData!.bb!.middle.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        bbMiddleRef.current.setData([])
      }
    }
    if (bbLowerRef.current) {
      bbLowerRef.current.applyOptions({ visible: hasBB })
      if (hasBB) {
        bbLowerRef.current.setData(
          indicatorData!.bb!.lower.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        bbLowerRef.current.setData([])
      }
    }

    // --- Dynamic scale margins ---
    const hasSubIndicator = (showRSI && !!indicatorData?.rsi?.series?.length) ||
                             (showMACD && !!indicatorData?.macd?.macdLine?.length)
    const bothSubIndicators = (showRSI && !!indicatorData?.rsi?.series?.length) &&
                               (showMACD && !!indicatorData?.macd?.macdLine?.length)

    // Adjust main price scale bottom margin to make room for sub-indicators
    const mainBottom = bothSubIndicators ? 0.45 : hasSubIndicator ? 0.35 : 0.3
    chart.applyOptions({
      rightPriceScale: {
        scaleMargins: { top: 0.1, bottom: mainBottom },
      },
    })

    // Adjust volume scale
    if (volumeSeriesRef.current) {
      const volTop = bothSubIndicators ? 0.55 : hasSubIndicator ? 0.65 : 0.8
      const volBottom = bothSubIndicators ? 0.45 : hasSubIndicator ? 0.35 : 0
      volumeSeriesRef.current.priceScale().applyOptions({
        scaleMargins: { top: volTop, bottom: volBottom },
      })
    }

    // Adjust sentiment scale
    if (sentimentSeriesRef.current) {
      sentimentSeriesRef.current.priceScale().applyOptions({
        scaleMargins: { top: 0.1, bottom: mainBottom },
      })
    }

    // RSI scale
    if (rsiSeriesRef.current && showRSI && indicatorData?.rsi?.series?.length) {
      const rsiTop = bothSubIndicators ? 0.6 : 0.7
      const rsiBottom = bothSubIndicators ? 0.25 : 0.05
      rsiSeriesRef.current.priceScale().applyOptions({
        scaleMargins: { top: rsiTop, bottom: rsiBottom },
      })
    }

    // MACD scale
    if (macdLineRef.current && showMACD && indicatorData?.macd?.macdLine?.length) {
      macdLineRef.current.priceScale().applyOptions({
        scaleMargins: { top: 0.85, bottom: 0 },
      })
    }
  }, [indicatorData, activeIndicators, data])

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
    rsi: undefined,
    macdValue: undefined,
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
              {displayData.rsi !== undefined && (
                <>
                  <span className="text-muted-foreground">RSI:</span>
                  <span className={cn(
                    'font-medium',
                    displayData.rsi > RSI_OVERBOUGHT ? 'text-stock-down' : displayData.rsi < RSI_OVERSOLD ? 'text-stock-up' : 'text-muted-foreground'
                  )}>
                    {displayData.rsi.toFixed(1)}
                  </span>
                </>
              )}
              {displayData.macdValue !== undefined && (
                <>
                  <span className="text-muted-foreground">MACD:</span>
                  <span className={cn(
                    'font-medium',
                    displayData.macdValue > 0 ? 'text-stock-up' : displayData.macdValue < 0 ? 'text-stock-down' : 'text-muted-foreground'
                  )}>
                    {displayData.macdValue.toFixed(4)}
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
