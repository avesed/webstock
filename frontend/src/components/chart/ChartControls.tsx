import { useState } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ChevronDown, Settings2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { ChartTimeframe } from '@/types'

interface ChartControlsProps {
  timeframe: ChartTimeframe
  onTimeframeChange: (timeframe: ChartTimeframe) => void
  interval?: ChartInterval
  onIntervalChange?: (interval: ChartInterval) => void
  indicators?: ChartIndicator[]
  onIndicatorToggle?: (indicator: ChartIndicator) => void
  maPeriods?: number[]
  className?: string
}

// Which timeframes are considered intraday (sentiment unavailable)
const INTRADAY_TIMEFRAMES: ChartTimeframe[] = ['1H', '1D', '1W']

export type ChartInterval = '1m' | '2m' | '5m' | '15m' | '30m' | '1h' | '1d' | '1wk' | '1mo'
export type ChartIndicator = 'MA' | 'RSI' | 'MACD' | 'BB' | 'VOL' | 'SENT'

const timeframes: { value: ChartTimeframe; label: string }[] = [
  { value: '1H', label: '1H' },
  { value: '1D', label: '1D' },
  { value: '1W', label: '1W' },
  { value: '1M', label: '1M' },
  { value: '3M', label: '3M' },
  { value: '6M', label: '6M' },
  { value: '1Y', label: '1Y' },
  { value: '5Y', label: '5Y' },
  { value: 'ALL', label: 'ALL' },
]

const intervals: { value: ChartInterval; label: string; description: string }[] = [
  { value: '1m', label: '1m', description: '1 minute' },
  { value: '2m', label: '2m', description: '2 minutes' },
  { value: '5m', label: '5m', description: '5 minutes' },
  { value: '15m', label: '15m', description: '15 minutes' },
  { value: '30m', label: '30m', description: '30 minutes' },
  { value: '1h', label: '1h', description: '1 hour' },
  { value: '1d', label: '1D', description: '1 day' },
  { value: '1wk', label: '1W', description: '1 week' },
  { value: '1mo', label: '1M', description: '1 month' },
]

// i18n keys for each indicator (must be literal strings for type-safe i18n)
const indicatorDefs: { value: ChartIndicator; labelKey: 'stock.indicators.ma' | 'stock.indicators.rsi' | 'stock.indicators.macd' | 'stock.indicators.bb' | 'stock.indicators.volume' | 'stock.indicators.sentiment'; descKey: 'stock.indicators.maDesc' | 'stock.indicators.rsiDesc' | 'stock.indicators.macdDesc' | 'stock.indicators.bbDesc' | 'stock.indicators.volumeDesc' | 'stock.indicators.sentimentDesc'; implemented: boolean }[] = [
  { value: 'MA', labelKey: 'stock.indicators.ma', descKey: 'stock.indicators.maDesc', implemented: true },
  { value: 'RSI', labelKey: 'stock.indicators.rsi', descKey: 'stock.indicators.rsiDesc', implemented: true },
  { value: 'MACD', labelKey: 'stock.indicators.macd', descKey: 'stock.indicators.macdDesc', implemented: true },
  { value: 'BB', labelKey: 'stock.indicators.bb', descKey: 'stock.indicators.bbDesc', implemented: true },
  { value: 'VOL', labelKey: 'stock.indicators.volume', descKey: 'stock.indicators.volumeDesc', implemented: true },
  { value: 'SENT', labelKey: 'stock.indicators.sentiment', descKey: 'stock.indicators.sentimentDesc', implemented: true },
]

export default function ChartControls({
  timeframe,
  onTimeframeChange,
  interval = '1d',
  onIntervalChange,
  indicators: activeIndicators = ['VOL'],
  onIndicatorToggle,
  maPeriods,
  className,
}: ChartControlsProps) {
  const { t } = useTranslation('dashboard')
  const selectedInterval = intervals.find((i) => i.value === interval)

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {/* Timeframe buttons */}
      <div className="flex items-center rounded-lg border bg-muted/50 p-1">
        {timeframes.map((tf) => (
          <Button
            key={tf.value}
            variant="ghost"
            size="sm"
            className={cn(
              'h-7 px-2.5 text-xs font-medium',
              timeframe === tf.value
                ? 'bg-background text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
            onClick={() => onTimeframeChange(tf.value)}
          >
            {tf.label}
          </Button>
        ))}
      </div>

      {/* Interval selector */}
      {onIntervalChange && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1">
              <span className="text-xs">{selectedInterval?.label ?? interval}</span>
              <ChevronDown className="h-3 w-3 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-48">
            <DropdownMenuLabel>{t('stock.indicators.chartInterval')}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {intervals.map((i) => (
              <DropdownMenuItem
                key={i.value}
                onClick={() => onIntervalChange(i.value)}
                className={cn(interval === i.value && 'bg-accent')}
              >
                <div className="flex flex-col">
                  <span className="font-medium">{i.label}</span>
                  <span className="text-xs text-muted-foreground">{i.description}</span>
                </div>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}

      {/* Indicators dropdown */}
      {onIndicatorToggle && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="h-8 gap-1">
              <Settings2 className="h-3.5 w-3.5" />
              <span className="hidden text-xs sm:inline">{t('stock.indicators.title')}</span>
              {activeIndicators.length > 0 && (
                <span className="ml-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
                  {activeIndicators.length}
                </span>
              )}
              <ChevronDown className="h-3 w-3 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuLabel>{t('stock.indicators.technicalIndicators')}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {indicatorDefs.map((indicator) => {
              const isActive = activeIndicators.includes(indicator.value)
              const isIntraday = INTRADAY_TIMEFRAMES.includes(timeframe)
              const isSentIntraday = indicator.value === 'SENT' && isIntraday
              const isDisabled = !indicator.implemented
              return (
                <DropdownMenuItem
                  key={indicator.value}
                  onClick={() => {
                    if (!isDisabled) onIndicatorToggle(indicator.value)
                  }}
                  className={cn(
                    'flex items-start gap-2',
                    isDisabled && 'opacity-40 cursor-not-allowed'
                  )}
                >
                  <div
                    className={cn(
                      'mt-0.5 h-4 w-4 rounded border',
                      isDisabled
                        ? 'border-muted'
                        : isActive ? 'border-primary bg-primary' : 'border-input'
                    )}
                  >
                    {isActive && !isDisabled && (
                      <svg
                        className="h-4 w-4 text-primary-foreground"
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={3}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M5 13l4 4L19 7"
                        />
                      </svg>
                    )}
                  </div>
                  <div className="flex flex-col">
                    <span className={cn('font-medium', isDisabled && 'text-muted-foreground')}>
                      {t(indicator.labelKey)}
                      {isDisabled && <span className="ml-1 text-[10px] font-normal">({t('stock.indicators.comingSoon')})</span>}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {t(indicator.descKey)}
                      {isSentIntraday && isActive && (
                        <span className="ml-1 text-amber-500">({t('stock.indicators.dailyOnly')})</span>
                      )}
                    </span>
                    {indicator.value === 'MA' && isActive && maPeriods && maPeriods.length > 0 && (
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        {maPeriods.map((p) => (
                          <span
                            key={p}
                            className="inline-flex items-center rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground"
                          >
                            {p}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </DropdownMenuItem>
              )
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

// Default interval for each timeframe (must stay in sync with api/index.ts TIMEFRAME_DEFAULT_INTERVAL)
const TIMEFRAME_DEFAULT_INTERVAL: Record<ChartTimeframe, ChartInterval> = {
  '1H': '1m',
  '1D': '5m',
  '1W': '15m',
  '1M': '1h',
  '3M': '1d',
  '6M': '1d',
  '1Y': '1d',
  '5Y': '1wk',
  'ALL': '1mo',
}

/**
 * Given the number of visible days on the chart, return the ideal interval.
 * Used by StockChart's zoom handler to auto-switch resolution.
 */
export function resolveInterval(visibleDays: number): ChartInterval {
  if (visibleDays <= 0.25) return '2m'     // < 6h
  if (visibleDays <= 2) return '5m'        // < 2 days
  if (visibleDays <= 7) return '15m'       // < 1 week
  if (visibleDays <= 60) return '1h'       // < 2 months
  if (visibleDays <= 730) return '1d'      // < 2 years
  if (visibleDays <= 1825) return '1wk'    // < 5 years
  return '1mo'
}

/** Visible time range for date-range (start/end) mode queries */
export interface VisibleRange {
  start: string
  end: string
}

// Export a hook for managing chart controls state
const TIMEFRAME_STORAGE_KEY = 'webstock-chart-timeframe'
const VALID_TIMEFRAMES: ChartTimeframe[] = ['1H', '1D', '1W', '1M', '3M', '6M', '1Y', '5Y', 'ALL']

export function useChartControls(initialTimeframe: ChartTimeframe = '1D') {
  const [timeframe, setTimeframeState] = useState<ChartTimeframe>(() => {
    try {
      const saved = localStorage.getItem(TIMEFRAME_STORAGE_KEY)
      if (saved && VALID_TIMEFRAMES.includes(saved as ChartTimeframe)) {
        return saved as ChartTimeframe
      }
    } catch {}
    return initialTimeframe
  })
  const [interval, setInterval] = useState<ChartInterval>(() =>
    TIMEFRAME_DEFAULT_INTERVAL[initialTimeframe]
  )
  const [visibleRange, setVisibleRange] = useState<VisibleRange | null>(null)
  const [activeIndicators, setActiveIndicators] = useState<ChartIndicator[]>(['VOL'])
  const [maPeriods, setMaPeriods] = useState<number[]>([20, 50, 200])

  const setTimeframe = (tf: ChartTimeframe) => {
    setTimeframeState(tf)
    // Reset interval to the default for the new timeframe and clear visible range
    setInterval(TIMEFRAME_DEFAULT_INTERVAL[tf])
    setVisibleRange(null)
    try { localStorage.setItem(TIMEFRAME_STORAGE_KEY, tf) } catch {}
  }

  const toggleIndicator = (indicator: ChartIndicator) => {
    setActiveIndicators((prev) =>
      prev.includes(indicator)
        ? prev.filter((i) => i !== indicator)
        : [...prev, indicator]
    )
  }

  return {
    timeframe,
    setTimeframe,
    interval,
    setInterval,
    visibleRange,
    setVisibleRange,
    activeIndicators,
    toggleIndicator,
    maPeriods,
    setMaPeriods,
  }
}

