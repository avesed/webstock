import { useEffect, useRef, useCallback, useState } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData as LWCandlestickData,
  type HistogramData,
  type Time,
  CrosshairMode,
  LineStyle,
} from 'lightweight-charts'
import { useTranslation } from 'react-i18next'
import { useThemeStore } from '@/stores/themeStore'
import type { CandlestickData, ChartTimeframe, SentimentTimelineItem, TechnicalIndicatorsData } from '@/types'
import type { ChartIndicator } from './ChartControls'
import { resolveInterval } from './ChartControls'
import { lightTheme, darkTheme, getChartColors } from './chartTheme'
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
  /** Live bar update from quote data. Applied via series.update() for smooth real-time updates. */
  latestBar?: CandlestickData | null
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

// Convert our data format to lightweight-charts format
function convertToChartData(data: CandlestickData[]): LWCandlestickData<Time>[] {
  if (!Array.isArray(data)) return []
  return data
    .filter((item) =>
      item.open != null && item.high != null && item.low != null && item.close != null
      // Guard against NaN timestamps from toMarketLocalTimestamp on invalid dates
      && (typeof item.time !== 'number' || Number.isFinite(item.time))
    )
    .map((item) => ({
      time: item.time as Time,
      open: item.open,
      high: item.high,
      low: item.low,
      close: item.close,
    }))
}

function convertToVolumeData(data: CandlestickData[], theme: 'light' | 'dark'): HistogramData<Time>[] {
  if (!Array.isArray(data)) return []
  const colors = getChartColors(theme)
  return data
    .filter((item) => item.volume !== undefined)
    .map((item) => ({
      time: item.time as Time,
      value: item.volume ?? 0,
      color: item.close >= item.open ? colors.upFill : colors.downFill,
    }))
}

// Theme-independent indicator constants
const RSI_OVERBOUGHT = 70
const RSI_OVERSOLD = 30

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
  latestBar,
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
  // New indicator series refs
  const atrSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const obvSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const kdjKRef = useRef<ISeriesApi<'Line'> | null>(null)
  const kdjDRef = useRef<ISeriesApi<'Line'> | null>(null)
  const kdjJRef = useRef<ISeriesApi<'Line'> | null>(null)
  const wrSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const cciSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const vwapSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
  const sarSeriesRef = useRef<ISeriesApi<'Line'> | null>(null)
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

    // Create candlestick series with theme-aware colors
    const colors = getChartColors(resolvedTheme === 'dark' ? 'dark' : 'light')
    const candlestickSeries = chart.addCandlestickSeries({
      upColor: colors.up,
      downColor: colors.down,
      borderUpColor: colors.up,
      borderDownColor: colors.down,
      wickUpColor: colors.up,
      wickDownColor: colors.down,
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

    // RSI series (theme-aware color)
    const rsiSeries = chart.addLineSeries({
      color: colors.rsiColor,
      lineWidth: 2,
      priceScaleId: 'rsi',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    rsiSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    rsiSeries.createPriceLine({ price: RSI_OVERBOUGHT, color: colors.rsiOverbought, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    rsiSeries.createPriceLine({ price: RSI_OVERSOLD, color: colors.rsiOversold, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    rsiSeries.applyOptions({ visible: false })
    rsiSeriesRef.current = rsiSeries

    // MACD series (theme-aware colors)
    const macdLine = chart.addLineSeries({
      color: colors.macdLineColor,
      lineWidth: 2,
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
      color: colors.macdSignalColor,
      lineWidth: 2,
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

    // Bollinger Bands (theme-aware colors, thicker lines)
    const bbUpper = chart.addLineSeries({
      color: colors.bbBandColor,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbUpper.applyOptions({ visible: false })
    bbUpperRef.current = bbUpper

    const bbMiddle = chart.addLineSeries({
      color: colors.bbColor,
      lineWidth: 2,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbMiddle.applyOptions({ visible: false })
    bbMiddleRef.current = bbMiddle

    const bbLower = chart.addLineSeries({
      color: colors.bbBandColor,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    bbLower.applyOptions({ visible: false })
    bbLowerRef.current = bbLower

    // ATR series (sub-chart, like RSI)
    const atrSeries = chart.addLineSeries({
      color: colors.atrColor,
      lineWidth: 2,
      priceScaleId: 'atr',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    atrSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    atrSeries.applyOptions({ visible: false })
    atrSeriesRef.current = atrSeries

    // OBV series (sub-chart)
    const obvSeries = chart.addLineSeries({
      color: colors.obvColor,
      lineWidth: 2,
      priceScaleId: 'obv',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    obvSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    obvSeries.applyOptions({ visible: false })
    obvSeriesRef.current = obvSeries

    // KDJ series (sub-chart, 3 lines + reference lines)
    const kdjK = chart.addLineSeries({
      color: colors.kdjKColor,
      lineWidth: 2,
      priceScaleId: 'kdj',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    kdjK.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    kdjK.createPriceLine({ price: 80, color: 'rgba(239, 68, 68, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    kdjK.createPriceLine({ price: 20, color: 'rgba(34, 197, 94, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    kdjK.applyOptions({ visible: false })
    kdjKRef.current = kdjK

    const kdjD = chart.addLineSeries({
      color: colors.kdjDColor,
      lineWidth: 2,
      priceScaleId: 'kdj',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    kdjD.applyOptions({ visible: false })
    kdjDRef.current = kdjD

    const kdjJ = chart.addLineSeries({
      color: colors.kdjJColor,
      lineWidth: 2,
      priceScaleId: 'kdj',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    kdjJ.applyOptions({ visible: false })
    kdjJRef.current = kdjJ

    // Williams %R series (sub-chart)
    const wrSeries = chart.addLineSeries({
      color: colors.wrColor,
      lineWidth: 2,
      priceScaleId: 'wr',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    wrSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    wrSeries.createPriceLine({ price: -20, color: 'rgba(239, 68, 68, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    wrSeries.createPriceLine({ price: -80, color: 'rgba(34, 197, 94, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    wrSeries.applyOptions({ visible: false })
    wrSeriesRef.current = wrSeries

    // CCI series (sub-chart)
    const cciSeries = chart.addLineSeries({
      color: colors.cciColor,
      lineWidth: 2,
      priceScaleId: 'cci',
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    cciSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
      visible: false,
    })
    cciSeries.createPriceLine({ price: 100, color: 'rgba(239, 68, 68, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    cciSeries.createPriceLine({ price: -100, color: 'rgba(34, 197, 94, 0.5)', lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false })
    cciSeries.applyOptions({ visible: false })
    cciSeriesRef.current = cciSeries

    // VWAP series (overlay on main chart, dashed line)
    const vwapSeries = chart.addLineSeries({
      color: colors.vwapColor,
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    })
    vwapSeries.applyOptions({ visible: false })
    vwapSeriesRef.current = vwapSeries

    // SAR series (overlay on main chart, dot markers)
    // Use lineWidth: 1 with lineVisible: false to show only point markers
    const sarSeries = chart.addLineSeries({
      color: colors.sarColor,
      lineWidth: 1,
      lineVisible: false,
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
      pointMarkersVisible: true,
      pointMarkersRadius: 2,
    })
    sarSeries.applyOptions({ visible: false })
    sarSeriesRef.current = sarSeries

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
      atrSeriesRef.current = null
      obvSeriesRef.current = null
      kdjKRef.current = null
      kdjDRef.current = null
      kdjJRef.current = null
      wrSeriesRef.current = null
      cciSeriesRef.current = null
      vwapSeriesRef.current = null
      sarSeriesRef.current = null
    }
  }, [height, resolvedTheme])

  // Update data when it changes
  useEffect(() => {
    if (!candlestickSeriesRef.current || !volumeSeriesRef.current || !Array.isArray(data) || data.length === 0) return

    const candleData = convertToChartData(data)
    const currentTheme = resolvedTheme === 'dark' ? 'dark' as const : 'light' as const
    const volumeData = convertToVolumeData(data, currentTheme)

    candlestickSeriesRef.current.setData(candleData)
    volumeSeriesRef.current.setData(volumeData)

    // Fit content to view (skip in zoom mode to prevent feedback loop)
    if (chartRef.current && !isZoomMode) {
      // For 1H timeframe, show the current clock hour (e.g., 13:00–14:00)
      // derived from the last bar's market-local timestamp.
      if (timeframe === '1H' && candleData.length > 0) {
        const lastTime = candleData[candleData.length - 1]!.time as number
        const hourStart = lastTime - (lastTime % 3600)
        chartRef.current.timeScale().setVisibleRange({
          from: hourStart as Time,
          to: (hourStart + 3600) as Time,
        })
      } else {
        chartRef.current.timeScale().fitContent()
      }
    }
  }, [data, isZoomMode, timeframe, resolvedTheme])

  // Apply live bar update via series.update() (avoids full setData redraw)
  useEffect(() => {
    if (!latestBar || !candlestickSeriesRef.current) return

    candlestickSeriesRef.current.update({
      time: latestBar.time as Time,
      open: latestBar.open,
      high: latestBar.high,
      low: latestBar.low,
      close: latestBar.close,
    })

    if (latestBar.volume != null && volumeSeriesRef.current) {
      const liveColors = getChartColors(resolvedTheme === 'dark' ? 'dark' : 'light')
      const color = latestBar.close >= latestBar.open ? liveColors.upFill : liveColors.downFill
      volumeSeriesRef.current.update({
        time: latestBar.time as Time,
        value: latestBar.volume,
        color,
      })
    }
  }, [latestBar, resolvedTheme])

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
    const showATR = !!activeIndicators?.includes('ATR')
    const showOBV = !!activeIndicators?.includes('OBV')
    const showKDJ = !!activeIndicators?.includes('KDJ')
    const showWR = !!activeIndicators?.includes('WR')
    const showCCI = !!activeIndicators?.includes('CCI')
    const showVWAP = !!activeIndicators?.includes('VWAP')
    const showSAR = !!activeIndicators?.includes('SAR')

    // Helper to convert indicator time format to chart time format
    const toChartTime = (point: { time: string | number }) => point.time as Time

    // --- MA Lines ---
    // Remove old MA series
    maSeriesRefs.current.forEach((s) => {
      try { chart.removeSeries(s) } catch { /* already removed */ }
    })
    maSeriesRefs.current.clear()

    if (showMA && indicatorData?.ma) {
      const themeColors = getChartColors(resolvedTheme === 'dark' ? 'dark' : 'light')
      const maKeys = Object.keys(indicatorData.ma)
      maKeys.forEach((key, idx) => {
        const maData = indicatorData.ma![key]
        if (!maData?.series?.length) return
        const color = themeColors.maColors[idx % themeColors.maColors.length]!
        const series = chart.addLineSeries({
          color,
          lineWidth: 2,
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
        const histColors = getChartColors(resolvedTheme === 'dark' ? 'dark' : 'light')
        macdHistRef.current.setData(
          indicatorData!.macd!.histogram.map(p => ({
            time: toChartTime(p),
            value: p.value,
            color: p.value >= 0 ? histColors.upFill : histColors.downFill,
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

    // --- ATR ---
    if (atrSeriesRef.current) {
      const hasATR = showATR && !!indicatorData?.atr?.series?.length
      atrSeriesRef.current.applyOptions({ visible: hasATR })
      atrSeriesRef.current.priceScale().applyOptions({ visible: hasATR })
      if (hasATR) {
        atrSeriesRef.current.setData(
          indicatorData!.atr!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        atrSeriesRef.current.setData([])
      }
    }

    // --- OBV ---
    if (obvSeriesRef.current) {
      const hasOBV = showOBV && !!indicatorData?.obv?.series?.length
      obvSeriesRef.current.applyOptions({ visible: hasOBV })
      obvSeriesRef.current.priceScale().applyOptions({ visible: hasOBV })
      if (hasOBV) {
        obvSeriesRef.current.setData(
          indicatorData!.obv!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        obvSeriesRef.current.setData([])
      }
    }

    // --- KDJ ---
    const hasKDJ = showKDJ && !!indicatorData?.kdj?.kLine?.length
    if (kdjKRef.current) {
      kdjKRef.current.applyOptions({ visible: hasKDJ })
      kdjKRef.current.priceScale().applyOptions({ visible: hasKDJ })
      if (hasKDJ) {
        kdjKRef.current.setData(
          indicatorData!.kdj!.kLine.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        kdjKRef.current.setData([])
      }
    }
    if (kdjDRef.current) {
      kdjDRef.current.applyOptions({ visible: hasKDJ })
      if (hasKDJ) {
        kdjDRef.current.setData(
          indicatorData!.kdj!.dLine.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        kdjDRef.current.setData([])
      }
    }
    if (kdjJRef.current) {
      kdjJRef.current.applyOptions({ visible: hasKDJ })
      if (hasKDJ) {
        kdjJRef.current.setData(
          indicatorData!.kdj!.jLine.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        kdjJRef.current.setData([])
      }
    }

    // --- Williams %R ---
    if (wrSeriesRef.current) {
      const hasWR = showWR && !!indicatorData?.williamsR?.series?.length
      wrSeriesRef.current.applyOptions({ visible: hasWR })
      wrSeriesRef.current.priceScale().applyOptions({ visible: hasWR })
      if (hasWR) {
        wrSeriesRef.current.setData(
          indicatorData!.williamsR!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        wrSeriesRef.current.setData([])
      }
    }

    // --- CCI ---
    if (cciSeriesRef.current) {
      const hasCCI = showCCI && !!indicatorData?.cci?.series?.length
      cciSeriesRef.current.applyOptions({ visible: hasCCI })
      cciSeriesRef.current.priceScale().applyOptions({ visible: hasCCI })
      if (hasCCI) {
        cciSeriesRef.current.setData(
          indicatorData!.cci!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        cciSeriesRef.current.setData([])
      }
    }

    // --- VWAP (overlay on main chart) ---
    if (vwapSeriesRef.current) {
      const hasVWAP = showVWAP && !!indicatorData?.vwap?.series?.length
      vwapSeriesRef.current.applyOptions({ visible: hasVWAP })
      if (hasVWAP) {
        vwapSeriesRef.current.setData(
          indicatorData!.vwap!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        vwapSeriesRef.current.setData([])
      }
    }

    // --- SAR (overlay on main chart, dots) ---
    if (sarSeriesRef.current) {
      const hasSAR = showSAR && !!indicatorData?.sar?.series?.length
      sarSeriesRef.current.applyOptions({ visible: hasSAR })
      if (hasSAR) {
        sarSeriesRef.current.setData(
          indicatorData!.sar!.series.map(p => ({ time: toChartTime(p), value: p.value }))
        )
      } else {
        sarSeriesRef.current.setData([])
      }
    }

    // --- Dynamic scale margins ---
    // Count active sub-chart indicators (excludes VWAP and SAR which overlay on main chart)
    let activeSubCount = 0
    if (showRSI && !!indicatorData?.rsi?.series?.length) activeSubCount++
    if (showMACD && !!indicatorData?.macd?.macdLine?.length) activeSubCount++
    if (showATR && !!indicatorData?.atr?.series?.length) activeSubCount++
    if (showOBV && !!indicatorData?.obv?.series?.length) activeSubCount++
    if (showKDJ && !!indicatorData?.kdj?.kLine?.length) activeSubCount++
    if (showWR && !!indicatorData?.williamsR?.series?.length) activeSubCount++
    if (showCCI && !!indicatorData?.cci?.series?.length) activeSubCount++

    // Adjust main price scale bottom margin to make room for sub-indicators
    // 0 sub-indicators: 0.3, 1: 0.35, 2: 0.45, 3+: 0.55 (capped)
    const mainBottom = activeSubCount >= 3 ? 0.55 : activeSubCount === 2 ? 0.45 : activeSubCount === 1 ? 0.35 : 0.3
    chart.applyOptions({
      rightPriceScale: {
        scaleMargins: { top: 0.1, bottom: mainBottom },
      },
    })

    // Adjust volume scale — volume bars sit just above the sub-indicator region.
    // Constraint: top + bottom MUST be < 1.0 (lightweight-charts requirement).
    // When no subs: volume fills bottom 15%. With subs: 10% band above sub region.
    if (volumeSeriesRef.current) {
      const volBottom = activeSubCount > 0 ? mainBottom : 0.05
      const volTop = Math.min(1.0 - volBottom - 0.10, 0.8) // at least 10% height, cap at 0.8
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

    // Distribute sub-chart indicators evenly in the bottom region
    // Each sub-indicator gets an equal slice of the bottom area
    if (activeSubCount > 0) {
      const subRegionStart = 1.0 - mainBottom  // Where the sub region begins (from top)
      const sliceHeight = mainBottom / activeSubCount
      let sliceIndex = 0

      // Helper: position a sub-indicator in its allocated slice
      const positionSubIndicator = (
        series: ISeriesApi<'Line'> | ISeriesApi<'Histogram'> | null,
        isActive: boolean
      ) => {
        if (!series || !isActive) return
        const top = subRegionStart + sliceIndex * sliceHeight
        const bottom = 1.0 - (subRegionStart + (sliceIndex + 1) * sliceHeight)
        series.priceScale().applyOptions({
          scaleMargins: { top: Math.min(top, 0.95), bottom: Math.max(bottom, 0) },
        })
        sliceIndex++
      }

      // Position each active sub-indicator in order
      if (showRSI && !!indicatorData?.rsi?.series?.length) {
        positionSubIndicator(rsiSeriesRef.current, true)
      }
      if (showMACD && !!indicatorData?.macd?.macdLine?.length) {
        positionSubIndicator(macdLineRef.current, true)
      }
      if (showATR && !!indicatorData?.atr?.series?.length) {
        positionSubIndicator(atrSeriesRef.current, true)
      }
      if (showOBV && !!indicatorData?.obv?.series?.length) {
        positionSubIndicator(obvSeriesRef.current, true)
      }
      if (showKDJ && !!indicatorData?.kdj?.kLine?.length) {
        positionSubIndicator(kdjKRef.current, true)
      }
      if (showWR && !!indicatorData?.williamsR?.series?.length) {
        positionSubIndicator(wrSeriesRef.current, true)
      }
      if (showCCI && !!indicatorData?.cci?.series?.length) {
        positionSubIndicator(cciSeriesRef.current, true)
      }
    }
  }, [indicatorData, activeIndicators, data, resolvedTheme])

  // Update theme when it changes (chart chrome + series colors)
  useEffect(() => {
    if (!chartRef.current) return

    const theme = resolvedTheme === 'dark' ? darkTheme : lightTheme
    const themeColors = getChartColors(resolvedTheme === 'dark' ? 'dark' : 'light')

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

    // Update candlestick colors
    if (candlestickSeriesRef.current) {
      candlestickSeriesRef.current.applyOptions({
        upColor: themeColors.up,
        downColor: themeColors.down,
        borderUpColor: themeColors.up,
        borderDownColor: themeColors.down,
        wickUpColor: themeColors.up,
        wickDownColor: themeColors.down,
      })
    }

    // Update BB colors
    if (bbUpperRef.current) bbUpperRef.current.applyOptions({ color: themeColors.bbBandColor })
    if (bbMiddleRef.current) bbMiddleRef.current.applyOptions({ color: themeColors.bbColor })
    if (bbLowerRef.current) bbLowerRef.current.applyOptions({ color: themeColors.bbBandColor })

    // Update RSI color
    if (rsiSeriesRef.current) rsiSeriesRef.current.applyOptions({ color: themeColors.rsiColor })

    // Update MACD colors
    if (macdLineRef.current) macdLineRef.current.applyOptions({ color: themeColors.macdLineColor })
    if (macdSignalRef.current) macdSignalRef.current.applyOptions({ color: themeColors.macdSignalColor })

    // Update new indicator colors
    if (atrSeriesRef.current) atrSeriesRef.current.applyOptions({ color: themeColors.atrColor })
    if (obvSeriesRef.current) obvSeriesRef.current.applyOptions({ color: themeColors.obvColor })
    if (kdjKRef.current) kdjKRef.current.applyOptions({ color: themeColors.kdjKColor })
    if (kdjDRef.current) kdjDRef.current.applyOptions({ color: themeColors.kdjDColor })
    if (kdjJRef.current) kdjJRef.current.applyOptions({ color: themeColors.kdjJColor })
    if (wrSeriesRef.current) wrSeriesRef.current.applyOptions({ color: themeColors.wrColor })
    if (cciSeriesRef.current) cciSeriesRef.current.applyOptions({ color: themeColors.cciColor })
    if (vwapSeriesRef.current) vwapSeriesRef.current.applyOptions({ color: themeColors.vwapColor })
    if (sarSeriesRef.current) sarSeriesRef.current.applyOptions({ color: themeColors.sarColor })
  }, [resolvedTheme])

  // Get the last data point for comparison (prefer latestBar for live updates)
  const lastDataPoint = Array.isArray(data) && data.length > 0 ? data[data.length - 1] : null
  const effectiveLastPoint = latestBar ?? lastDataPoint
  const displayData = crosshairData ?? (effectiveLastPoint ? {
    time: effectiveLastPoint.time,
    open: effectiveLastPoint.open,
    high: effectiveLastPoint.high,
    low: effectiveLastPoint.low,
    close: effectiveLastPoint.close,
    volume: effectiveLastPoint.volume,
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
      <div className="absolute left-4 top-2 z-10 rounded-lg bg-background/90 p-2 sm:p-3 text-xs sm:text-sm shadow-sm backdrop-blur-sm">
        <div className="mb-1 font-semibold">{symbol}</div>
        {displayData ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="text-muted-foreground">{t('stock.ohlc.open')}:</span>
              <span>{formatPrice(displayData.open)}</span>
              <span className="hidden sm:inline text-muted-foreground">{t('stock.ohlc.high')}:</span>
              <span className="hidden sm:inline">{formatPrice(displayData.high)}</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="hidden sm:inline text-muted-foreground">{t('stock.ohlc.low')}:</span>
              <span className="hidden sm:inline">{formatPrice(displayData.low)}</span>
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
