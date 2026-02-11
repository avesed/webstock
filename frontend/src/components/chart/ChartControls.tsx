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
  className?: string
}

// Which timeframes are considered intraday (sentiment unavailable)
const INTRADAY_TIMEFRAMES: ChartTimeframe[] = ['1H', '1D', '1W']

export type ChartInterval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d' | '1w'
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
  { value: '5m', label: '5m', description: '5 minutes' },
  { value: '15m', label: '15m', description: '15 minutes' },
  { value: '1h', label: '1h', description: '1 hour' },
  { value: '4h', label: '4h', description: '4 hours' },
  { value: '1d', label: '1D', description: '1 day' },
  { value: '1w', label: '1W', description: '1 week' },
]

// i18n keys for each indicator (must be literal strings for type-safe i18n)
const indicatorDefs: { value: ChartIndicator; labelKey: 'stock.indicators.ma' | 'stock.indicators.rsi' | 'stock.indicators.macd' | 'stock.indicators.bb' | 'stock.indicators.volume' | 'stock.indicators.sentiment'; descKey: 'stock.indicators.maDesc' | 'stock.indicators.rsiDesc' | 'stock.indicators.macdDesc' | 'stock.indicators.bbDesc' | 'stock.indicators.volumeDesc' | 'stock.indicators.sentimentDesc'; implemented: boolean }[] = [
  { value: 'MA', labelKey: 'stock.indicators.ma', descKey: 'stock.indicators.maDesc', implemented: false },
  { value: 'RSI', labelKey: 'stock.indicators.rsi', descKey: 'stock.indicators.rsiDesc', implemented: false },
  { value: 'MACD', labelKey: 'stock.indicators.macd', descKey: 'stock.indicators.macdDesc', implemented: false },
  { value: 'BB', labelKey: 'stock.indicators.bb', descKey: 'stock.indicators.bbDesc', implemented: false },
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

// Export a hook for managing chart controls state
const TIMEFRAME_STORAGE_KEY = 'webstock-chart-timeframe'
const VALID_TIMEFRAMES: ChartTimeframe[] = ['1H', '1D', '1W', '1M', '3M', '6M', '1Y', '5Y', 'ALL']

export function useChartControls(initialTimeframe: ChartTimeframe = '1M') {
  const [timeframe, setTimeframeState] = useState<ChartTimeframe>(() => {
    try {
      const saved = localStorage.getItem(TIMEFRAME_STORAGE_KEY)
      if (saved && VALID_TIMEFRAMES.includes(saved as ChartTimeframe)) {
        return saved as ChartTimeframe
      }
    } catch {}
    return initialTimeframe
  })
  const [interval, setInterval] = useState<ChartInterval>('1d')
  const [activeIndicators, setActiveIndicators] = useState<ChartIndicator[]>(['VOL'])

  const setTimeframe = (tf: ChartTimeframe) => {
    setTimeframeState(tf)
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
    activeIndicators,
    toggleIndicator,
  }
}

// Need to import useState for the hook
import { useState } from 'react'
