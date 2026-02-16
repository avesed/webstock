import { useRef, useEffect, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { createChart, type IChartApi, type ISeriesApi, type Time, CrosshairMode } from 'lightweight-charts'
import { Loader2 } from 'lucide-react'
import { stockApi } from '@/api'
import { useThemeStore } from '@/stores/themeStore'
import { lightTheme, darkTheme, getChartColors } from '@/components/chart/chartTheme'
import { useIsMobile } from '@/hooks/useIsMobile'
import { cn, formatCurrency, formatPercent, getPriceChangeColor } from '@/lib/utils'
import type { Portfolio, Holding } from '@/types'

interface PortfolioMiniChartProps {
  portfolios: Portfolio[] | undefined
}

interface PortfolioTimeSeriesPoint {
  time: string
  value: number
}

/** Maximum number of holdings to fetch history for, to limit API calls. */
const MAX_HOLDINGS_FOR_CHART = 8

/**
 * Select the best portfolio to display: prefer the one with the most holdings,
 * falling back to the first in the list.
 */
function selectPortfolio(portfolios: Portfolio[]): Portfolio | undefined {
  if (portfolios.length === 0) return undefined
  let best = portfolios[0]!
  for (const p of portfolios) {
    if ((p.holdings?.length ?? 0) > (best.holdings?.length ?? 0)) {
      best = p
    }
  }
  return best
}

/**
 * Compute a daily portfolio value time series from per-holding history data.
 *
 * For each unique date across all holdings, the portfolio value is the sum of
 * each holding's quantity multiplied by its closing price on that date.
 * Missing dates for a symbol are forward-filled with the last known close.
 */
function computePortfolioTimeSeries(
  holdings: Holding[],
  historyMap: Map<string, { time: string; close: number }[]>,
): PortfolioTimeSeriesPoint[] {
  // Collect all unique dates across all symbols
  const dateSet = new Set<string>()
  for (const bars of historyMap.values()) {
    for (const bar of bars) {
      dateSet.add(bar.time)
    }
  }

  if (dateSet.size === 0) return []

  const sortedDates = Array.from(dateSet).sort()

  // Build a date->close lookup per symbol for O(1) access
  const symbolCloseMaps = new Map<string, Map<string, number>>()
  for (const [symbol, bars] of historyMap) {
    const closeMap = new Map<string, number>()
    for (const bar of bars) {
      closeMap.set(bar.time, bar.close)
    }
    symbolCloseMaps.set(symbol, closeMap)
  }

  // For each date, compute total portfolio value using forward-fill
  const result: PortfolioTimeSeriesPoint[] = []
  const lastKnownClose = new Map<string, number>()

  for (const date of sortedDates) {
    let totalValue = 0

    for (const holding of holdings) {
      const closeMap = symbolCloseMaps.get(holding.symbol)
      if (!closeMap) {
        if (import.meta.env.DEV) {
          console.warn(`[PortfolioMiniChart] No history for ${holding.symbol}, using averageCost fallback`)
        }
        totalValue += holding.quantity * holding.averageCost
        continue
      }

      const closeOnDate = closeMap.get(date)
      if (closeOnDate != null) {
        lastKnownClose.set(holding.symbol, closeOnDate)
        totalValue += holding.quantity * closeOnDate
      } else {
        // Forward-fill: use last known close, or averageCost if nothing yet
        const fallback = lastKnownClose.get(holding.symbol) ?? holding.averageCost
        totalValue += holding.quantity * fallback
      }
    }

    result.push({ time: date, value: totalValue })
  }

  return result
}

export default function PortfolioMiniChart({ portfolios }: PortfolioMiniChartProps) {
  const { t } = useTranslation('dashboard')
  const { resolvedTheme } = useThemeStore()
  const isMobile = useIsMobile()
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Area'> | null>(null)

  const chartHeight = isMobile ? 120 : 160

  // Select the best portfolio
  const portfolio = useMemo(() => {
    if (!portfolios || portfolios.length === 0) return undefined
    return selectPortfolio(portfolios)
  }, [portfolios])

  // Extract holding symbols (capped)
  const holdingSymbols = useMemo(() => {
    if (!portfolio?.holdings) return []
    return portfolio.holdings
      .slice(0, MAX_HOLDINGS_FOR_CHART)
      .map((h) => h.symbol)
  }, [portfolio])

  // Fetch 1-month daily history for each holding
  const { data: chartData, isLoading: isChartLoading } = useQuery({
    queryKey: ['portfolio-chart', holdingSymbols],
    queryFn: async () => {
      if (!portfolio) return []

      const historyMap = new Map<string, { time: string; close: number }[]>()
      await Promise.all(
        holdingSymbols.map(async (symbol) => {
          try {
            const history = await stockApi.getHistory(symbol, '1M')
            historyMap.set(
              symbol,
              history.map((bar) => ({
                time: String(bar.time),
                close: bar.close,
              })),
            )
          } catch (err) {
            console.warn(`[PortfolioMiniChart] Failed to fetch history for ${symbol}:`, err)
          }
        }),
      )
      return computePortfolioTimeSeries(portfolio.holdings, historyMap)
    },
    enabled: holdingSymbols.length > 0,
    staleTime: 5 * 60 * 1000,
  })

  // Compute 1M change stats from chart data
  const changeStats = useMemo(() => {
    if (!chartData || chartData.length < 2) return null
    const firstValue = chartData[0]!.value
    const lastValue = chartData[chartData.length - 1]!.value
    const changeAmount = lastValue - firstValue
    const changePercent = firstValue !== 0 ? (changeAmount / firstValue) * 100 : 0
    return { currentValue: lastValue, changeAmount, changePercent }
  }, [chartData])

  // Determine trend direction for coloring
  const isUpTrend = useMemo(() => {
    if (!chartData || chartData.length < 2) return true
    return chartData[chartData.length - 1]!.value > chartData[0]!.value
  }, [chartData])

  // Create and manage chart
  useEffect(() => {
    if (!chartContainerRef.current || !chartData || chartData.length < 2) return

    const container = chartContainerRef.current
    const themeOptions = resolvedTheme === 'dark' ? darkTheme : lightTheme
    const colors = getChartColors(resolvedTheme)

    const chart = createChart(container, {
      ...themeOptions,
      width: container.clientWidth,
      height: chartHeight,
      rightPriceScale: { visible: false },
      timeScale: { visible: false },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      handleScroll: false,
      handleScale: false,
      crosshair: { mode: CrosshairMode.Hidden },
    })

    const series = chart.addAreaSeries({
      lineColor: isUpTrend ? colors.up : colors.down,
      topColor: isUpTrend ? colors.areaUpTop : colors.areaDownTop,
      bottomColor: isUpTrend ? colors.areaUpBottom : colors.areaDownBottom,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    })

    series.setData(
      chartData.map((d) => ({
        time: d.time as Time,
        value: d.value,
      })),
    )

    chart.timeScale().fitContent()

    chartRef.current = chart
    seriesRef.current = series

    // ResizeObserver for responsive sizing
    const resizeObserver = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width } = entry.contentRect
        if (width > 0) {
          chart.applyOptions({ width })
        }
      }
    })
    resizeObserver.observe(container)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [chartData, chartHeight, isUpTrend, resolvedTheme])

  // ---- Empty states ----

  if (!portfolios || portfolios.length === 0) {
    return (
      <div className="h-full rounded-lg border bg-card p-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          {t('portfolioChart.title')}
        </h3>
        <div className="flex items-center justify-center py-6">
          <span className="text-sm text-muted-foreground">
            {t('portfolioChart.noPortfolio')}
          </span>
        </div>
      </div>
    )
  }

  if (!portfolio || !portfolio.holdings || portfolio.holdings.length === 0) {
    return (
      <div className="h-full rounded-lg border bg-card p-3">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          {t('portfolioChart.title')}
        </h3>
        <div className="flex items-center justify-center py-6">
          <span className="text-sm text-muted-foreground">
            {t('portfolioChart.noHoldings')}
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border bg-card p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        {t('portfolioChart.title')}
      </h3>

      {isChartLoading ? (
        <div
          className="flex items-center justify-center"
          style={{ height: chartHeight }}
        >
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !chartData || chartData.length < 2 ? (
        <div
          className="flex items-center justify-center"
          style={{ height: chartHeight }}
        >
          <span className="text-sm text-muted-foreground">
            {t('portfolioChart.noData')}
          </span>
        </div>
      ) : (
        <>
          <div
            ref={chartContainerRef}
            className="w-full"
            style={{ height: chartHeight }}
          />

          {changeStats && (
            <div className="flex items-center gap-3 mt-2">
              <span className="text-sm font-mono font-semibold">
                {formatCurrency(changeStats.currentValue)}
              </span>
              <span
                className={cn(
                  'text-xs font-mono',
                  getPriceChangeColor(changeStats.changeAmount),
                )}
              >
                {changeStats.changeAmount >= 0 ? '+' : ''}
                {formatCurrency(changeStats.changeAmount)}
              </span>
              <span
                className={cn(
                  'text-xs font-mono',
                  getPriceChangeColor(changeStats.changePercent),
                )}
              >
                ({formatPercent(changeStats.changePercent, 1)})
              </span>
            </div>
          )}
        </>
      )}
    </div>
  )
}
