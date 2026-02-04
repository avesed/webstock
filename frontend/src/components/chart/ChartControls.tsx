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

export type ChartInterval = '1m' | '5m' | '15m' | '1h' | '4h' | '1d' | '1w'
export type ChartIndicator = 'MA' | 'RSI' | 'MACD' | 'BB' | 'VOL'

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

const indicators: { value: ChartIndicator; label: string; description: string }[] = [
  { value: 'MA', label: 'Moving Average', description: 'Simple and exponential moving averages' },
  { value: 'RSI', label: 'RSI', description: 'Relative Strength Index' },
  { value: 'MACD', label: 'MACD', description: 'Moving Average Convergence Divergence' },
  { value: 'BB', label: 'Bollinger Bands', description: 'Volatility bands' },
  { value: 'VOL', label: 'Volume', description: 'Trading volume histogram' },
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
            <DropdownMenuLabel>Chart Interval</DropdownMenuLabel>
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
              <span className="hidden text-xs sm:inline">Indicators</span>
              {activeIndicators.length > 0 && (
                <span className="ml-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
                  {activeIndicators.length}
                </span>
              )}
              <ChevronDown className="h-3 w-3 opacity-50" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuLabel>Technical Indicators</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {indicators.map((indicator) => {
              const isActive = activeIndicators.includes(indicator.value)
              return (
                <DropdownMenuItem
                  key={indicator.value}
                  onClick={() => onIndicatorToggle(indicator.value)}
                  className="flex items-start gap-2"
                >
                  <div
                    className={cn(
                      'mt-0.5 h-4 w-4 rounded border',
                      isActive ? 'border-primary bg-primary' : 'border-input'
                    )}
                  >
                    {isActive && (
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
                    <span className="font-medium">{indicator.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {indicator.description}
                    </span>
                  </div>
                </DropdownMenuItem>
              )
            })}
            <DropdownMenuSeparator />
            <div className="px-2 py-1.5 text-xs text-muted-foreground">
              Technical indicators coming soon
            </div>
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
