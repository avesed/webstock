import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const DEBOUNCE_MS = 300

const SENTIMENT_OPTIONS = [
  { value: 'bullish', key: 'news.filter.bullish' },
  { value: 'bearish', key: 'news.filter.bearish' },
  { value: 'neutral', key: 'news.filter.neutral' },
] as const
type SentimentOption = 'bullish' | 'bearish' | 'neutral'

const MARKET_OPTIONS = [
  { value: 'US', key: 'news.filter.us' },
  { value: 'HK', key: 'news.filter.hk' },
  { value: 'CN', key: 'news.filter.cn' },
] as const
type MarketOption = 'US' | 'HK' | 'CN'

export interface NewsFilters {
  search: string
  sentimentTag: SentimentOption | null
  market: MarketOption | null
}

interface NewsFilterBarProps {
  filters: NewsFilters
  onFiltersChange: (filters: NewsFilters) => void
  showMarketFilter: boolean
  className?: string
}

const SENTIMENT_COLORS: Record<SentimentOption, { active: string; text: string }> = {
  bullish: { active: 'bg-green-500/15 text-green-600 dark:text-green-400 hover:bg-green-500/20', text: 'text-green-600 dark:text-green-400' },
  bearish: { active: 'bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/20', text: 'text-red-600 dark:text-red-400' },
  neutral: { active: 'bg-blue-500/15 text-blue-600 dark:text-blue-400 hover:bg-blue-500/20', text: 'text-blue-600 dark:text-blue-400' },
}

const MARKET_COLORS: Record<MarketOption, string> = {
  US: 'bg-blue-500/15 text-blue-600 dark:text-blue-400 hover:bg-blue-500/20',
  HK: 'bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/20',
  CN: 'bg-amber-500/15 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20',
}

export default function NewsFilterBar({
  filters,
  onFiltersChange,
  showMarketFilter,
  className,
}: NewsFilterBarProps) {
  const { t } = useTranslation('dashboard')
  const [searchInput, setSearchInput] = useState(filters.search)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>()

  // Sync external search changes (e.g. URL restore, clear all)
  useEffect(() => {
    setSearchInput(filters.search)
  }, [filters.search])

  // Debounced search propagation
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const trimmed = searchInput.trim()
      if (trimmed !== filters.search) {
        onFiltersChange({ ...filters, search: trimmed })
      }
    }, DEBOUNCE_MS)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
  }, [searchInput]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleSentiment = (value: SentimentOption) => {
    onFiltersChange({
      ...filters,
      sentimentTag: filters.sentimentTag === value ? null : value,
    })
  }

  const toggleMarket = (value: MarketOption) => {
    onFiltersChange({
      ...filters,
      market: filters.market === value ? null : value,
    })
  }

  const activeCount =
    (filters.search ? 1 : 0) +
    (filters.sentimentTag ? 1 : 0) +
    (filters.market ? 1 : 0)

  const clearAll = () => {
    setSearchInput('')
    onFiltersChange({ search: '', sentimentTag: null, market: null })
  }

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {/* Search input */}
      <div className="relative flex-1 min-w-[180px] max-w-xs">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
        <input
          type="text"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
          placeholder={t('news.filter.searchPlaceholder')}
          className="flex h-8 w-full rounded-md border border-input bg-background pl-8 pr-8 py-1.5 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        {searchInput && (
          <button
            type="button"
            onClick={() => {
              setSearchInput('')
              onFiltersChange({ ...filters, search: '' })
            }}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {/* Divider */}
      <div className="h-5 w-px bg-border hidden sm:block" />

      {/* Sentiment chips */}
      <div className="flex items-center gap-1">
        {SENTIMENT_OPTIONS.map(({ value, key }) => (
          <Button
            key={value}
            variant="ghost"
            size="sm"
            onClick={() => toggleSentiment(value)}
            className={cn(
              'h-7 px-2.5 text-xs font-medium rounded-full',
              filters.sentimentTag === value
                ? SENTIMENT_COLORS[value].active
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t(key)}
          </Button>
        ))}
      </div>

      {/* Market chips (Market tab only) */}
      {showMarketFilter && (
        <>
          <div className="h-5 w-px bg-border hidden sm:block" />
          <div className="flex items-center gap-1">
            {MARKET_OPTIONS.map(({ value, key }) => (
              <Button
                key={value}
                variant="ghost"
                size="sm"
                onClick={() => toggleMarket(value)}
                className={cn(
                  'h-7 px-2.5 text-xs font-medium rounded-full',
                  filters.market === value
                    ? MARKET_COLORS[value]
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {t(key)}
              </Button>
            ))}
          </div>
        </>
      )}

      {/* Clear button */}
      {activeCount > 0 && (
        <Button
          variant="ghost"
          size="sm"
          onClick={clearAll}
          className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
        >
          <X className="h-3 w-3 mr-1" />
          {t('news.filter.clear')}
          <span className="ml-1 inline-flex items-center justify-center rounded-full bg-muted px-1.5 text-[10px] font-medium">
            {activeCount}
          </span>
        </Button>
      )}
    </div>
  )
}
