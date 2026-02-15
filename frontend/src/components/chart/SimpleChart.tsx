import { useEffect, useRef, useState, useCallback } from 'react'
import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  CrosshairMode,
} from 'lightweight-charts'
import { useTranslation } from 'react-i18next'
import { useThemeStore } from '@/stores/themeStore'
import type { CandlestickData, ChartTimeframe } from '@/types'
import { lightTheme, darkTheme, getChartColors } from './chartTheme'
import { cn } from '@/lib/utils'
import { Maximize2 } from 'lucide-react'

interface SimpleChartProps {
  data: CandlestickData[]
  latestBar?: CandlestickData | null
  timeframe: ChartTimeframe
  symbol: string
  isLoading?: boolean
  height?: number
  onExpand?: () => void
  className?: string
}

/** Convert CandlestickData to area series format { time, value } */
function convertToAreaData(data: CandlestickData[]): { time: Time; value: number }[] {
  if (!Array.isArray(data)) return []
  return data
    .filter((item) => item.close != null)
    .map((item) => ({ time: item.time as Time, value: item.close }))
}

/** Format price with appropriate decimal places */
function formatPrice(price: number): string {
  if (price >= 1000) return price.toFixed(0)
  if (price >= 1) return price.toFixed(2)
  return price.toFixed(4)
}

/** Format time for tooltip display */
function formatTime(time: string | number, timeframe: ChartTimeframe): string {
  if (typeof time === 'number') {
    const date = new Date(time * 1000)
    if (['1D', '1W'].includes(timeframe)) {
      return date.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      })
    }
    return date.toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    })
  }
  // ISO string with timezone
  if (typeof time === 'string' && time.includes('T')) {
    const date = new Date(time)
    if (['1D', '1W'].includes(timeframe)) {
      return date.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
      })
    }
    return date.toLocaleDateString(undefined, {
      year: 'numeric', month: 'short', day: 'numeric',
    })
  }
  // Date string like "2024-01-15"
  return String(time)
}

export default function SimpleChart({
  data,
  latestBar,
  timeframe,
  symbol,
  isLoading = false,
  height = 280,
  onExpand,
  className,
}: SimpleChartProps) {
  const { t } = useTranslation('dashboard')
  const { resolvedTheme } = useThemeStore()
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const areaSeriesRef = useRef<ISeriesApi<'Area'> | null>(null)
  const [tooltipData, setTooltipData] = useState<{ time: string | number; value: number } | null>(null)

  // Determine if the trend is up or down (compare first vs last close)
  const getDirection = useCallback((chartData: CandlestickData[], live?: CandlestickData | null): 'up' | 'down' => {
    if (!chartData.length) return 'up'
    const firstClose = chartData[0]!.close
    const lastClose = live?.close ?? chartData[chartData.length - 1]!.close
    return lastClose >= firstClose ? 'up' : 'down'
  }, [])

  // Apply direction-based colors to the area series
  const applyDirectionColors = useCallback((series: ISeriesApi<'Area'>, direction: 'up' | 'down', theme: string) => {
    const colors = getChartColors(theme === 'dark' ? 'dark' : 'light')
    if (direction === 'up') {
      series.applyOptions({
        lineColor: colors.up,
        topColor: colors.areaUpTop,
        bottomColor: colors.areaUpBottom,
      })
    } else {
      series.applyOptions({
        lineColor: colors.down,
        topColor: colors.areaDownTop,
        bottomColor: colors.areaDownBottom,
      })
    }
  }, [])

  // Create chart instance
  useEffect(() => {
    if (!chartContainerRef.current) return

    const theme = resolvedTheme === 'dark' ? darkTheme : lightTheme

    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height,
      ...theme,
      layout: {
        ...theme.layout,
        attributionLogo: false,
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: theme.grid.horzLines.color, style: 1 },
      },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: { top: 0.1, bottom: 0.05 },
      },
      timeScale: {
        visible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: {
          ...theme.crosshair.vertLine,
          width: 1,
          style: 3,
          labelVisible: false,
        },
        horzLine: {
          ...theme.crosshair.horzLine,
          width: 1,
          style: 3,
        },
      },
      handleScroll: { vertTouchDrag: false },
    })

    const areaSeries = chart.addAreaSeries({
      lineWidth: 2,
      lastValueVisible: true,
      crosshairMarkerVisible: true,
      crosshairMarkerRadius: 4,
      priceLineVisible: false,
    })

    // Crosshair tooltip
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.seriesData || param.seriesData.size === 0) {
        setTooltipData(null)
        return
      }
      const areaData = param.seriesData.get(areaSeries) as { time?: Time; value?: number } | undefined
      if (areaData?.value != null) {
        setTooltipData({ time: param.time as string | number, value: areaData.value })
      }
    })

    // ResizeObserver for responsive width
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        })
      }
    }
    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(chartContainerRef.current)

    chartRef.current = chart
    areaSeriesRef.current = areaSeries

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      areaSeriesRef.current = null
    }
  }, [height, resolvedTheme])

  // Update data
  useEffect(() => {
    if (!areaSeriesRef.current || !Array.isArray(data) || data.length === 0) return

    const areaData = convertToAreaData(data)
    areaSeriesRef.current.setData(areaData)

    // Apply direction colors
    const direction = getDirection(data, latestBar)
    applyDirectionColors(areaSeriesRef.current, direction, resolvedTheme)

    if (chartRef.current) {
      chartRef.current.timeScale().fitContent()
    }
  }, [data, timeframe, resolvedTheme, getDirection, applyDirectionColors, latestBar])

  // Real-time update via series.update()
  useEffect(() => {
    if (!areaSeriesRef.current || !latestBar) return

    areaSeriesRef.current.update({
      time: latestBar.time as Time,
      value: latestBar.close,
    })

    // Re-check direction in case it flipped
    const direction = getDirection(data, latestBar)
    applyDirectionColors(areaSeriesRef.current, direction, resolvedTheme)
  }, [latestBar, data, resolvedTheme, getDirection, applyDirectionColors])

  // Theme change: update chart options
  useEffect(() => {
    if (!chartRef.current) return
    const theme = resolvedTheme === 'dark' ? darkTheme : lightTheme
    chartRef.current.applyOptions({
      layout: theme.layout,
      grid: {
        vertLines: { visible: false },
        horzLines: { color: theme.grid.horzLines.color },
      },
      crosshair: {
        vertLine: { ...theme.crosshair.vertLine },
        horzLine: { ...theme.crosshair.horzLine },
      },
    })
  }, [resolvedTheme])

  // Derive display data: prefer tooltip (crosshair hover), fall back to latest point
  const lastDataPoint = Array.isArray(data) && data.length > 0 ? data[data.length - 1] : null
  const effectiveLastPoint = latestBar ?? lastDataPoint
  const displayData = tooltipData ?? (effectiveLastPoint ? { time: effectiveLastPoint.time, value: effectiveLastPoint.close } : null)

  return (
    <div className={cn('relative', className)}>
      {/* Tooltip overlay — top-left */}
      <div className="absolute left-3 top-2 z-10 rounded-md bg-background/90 px-2.5 py-1.5 text-sm backdrop-blur-sm">
        {displayData ? (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-xs">
              {formatTime(displayData.time, timeframe)}
            </span>
            <span className="font-medium text-foreground">
              {formatPrice(displayData.value)}
            </span>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">{symbol}</span>
        )}
      </div>

      {/* Expand button — top-right */}
      {onExpand && (
        <button
          onClick={onExpand}
          className="absolute right-3 top-2 z-10 rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
          title={t('stock.expandChart')}
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      )}

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
          </div>
        </div>
      )}
    </div>
  )
}
