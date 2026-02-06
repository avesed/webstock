import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import axios from 'axios'
import { Search, X, Clock, TrendingUp, Loader2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useStockStore } from '@/stores/stockStore'
import { stockApi } from '@/api'
import type { SearchResult, Market } from '@/types'

interface StockSearchProps {
  placeholder?: string
  className?: string
  onSelect?: (result: SearchResult) => void
  autoFocus?: boolean
  showRecentSearches?: boolean
}

const DEBOUNCE_MS = 100

// Market flag/label mapping (SH/SZ/BJ are Shanghai/Shenzhen/Beijing A-shares)
const marketLabels: Record<string, { label: string; color: string }> = {
  US: { label: 'US', color: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  HK: { label: 'HK', color: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  CN: { label: 'CN', color: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  SH: { label: 'SH', color: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  SZ: { label: 'SZ', color: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
  BJ: { label: 'BJ', color: 'bg-purple-500/10 text-purple-600 dark:text-purple-400' },
  METAL: { label: 'METAL', color: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-200' },
}

// Default market label for unknown markets
const defaultMarketLabel = { label: '??', color: 'bg-gray-500/10 text-gray-600 dark:text-gray-400' }

/**
 * Highlights matching text within a string
 * @param text - The text to search in
 * @param query - The search query to highlight
 * @returns ReactNode with highlighted matching portion
 */
function highlightMatch(text: string, query: string): ReactNode {
  if (!query || !text) return text

  const queryLower = query.toLowerCase()
  const textLower = text.toLowerCase()
  const index = textLower.indexOf(queryLower)

  if (index === -1) return text

  return (
    <>
      {text.slice(0, index)}
      <mark className="bg-yellow-200 dark:bg-yellow-800 rounded px-0.5">
        {text.slice(index, index + query.length)}
      </mark>
      {text.slice(index + query.length)}
    </>
  )
}

/**
 * Renders the symbol with optional highlighting based on match field
 */
function renderSymbol(result: SearchResult, query: string): ReactNode {
  if (result.matchField === 'symbol') {
    return highlightMatch(result.symbol, query)
  }
  return result.symbol
}

/**
 * Renders the name/description line with appropriate highlighting based on match field
 */
function renderName(result: SearchResult, query: string): ReactNode {
  const matchField = result.matchField
  const displayName = result.name || result.exchange

  // Highlight Chinese name if that's what matched
  if (matchField === 'name_zh' && result.nameZh) {
    return (
      <>
        {highlightMatch(result.nameZh, query)}
        {result.name && <span className="ml-1 text-xs opacity-70">({result.name})</span>}
      </>
    )
  }

  // Highlight English name if that's what matched
  if (matchField === 'name') {
    return (
      <>
        {highlightMatch(displayName, query)}
        {result.nameZh && <span className="ml-1 text-xs opacity-70">({result.nameZh})</span>}
      </>
    )
  }

  // For pinyin matches, show the Chinese name with a pinyin indicator
  if ((matchField === 'pinyin' || matchField === 'pinyin_initial') && result.nameZh) {
    return (
      <>
        {result.nameZh}
        <span className="ml-1 text-xs opacity-70" title="Matched by pinyin">
          ({displayName})
        </span>
      </>
    )
  }

  // Default: show name with optional Chinese name suffix
  return (
    <>
      {displayName}
      {result.nameZh && <span className="ml-1 text-xs opacity-70">({result.nameZh})</span>}
    </>
  )
}

export default function StockSearch({
  placeholder,
  className,
  onSelect,
  autoFocus = false,
  showRecentSearches = true,
}: StockSearchProps) {
  const { t } = useTranslation('dashboard')
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
  const defaultPlaceholder = placeholder ?? t('search.placeholder')
  const containerRef = useRef<HTMLDivElement>(null)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [isOpen, setIsOpen] = useState(false)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const [error, setError] = useState<string | null>(null)

  const {
    recentSearches,
    addRecentSearch,
    clearRecentSearches,
    setSelectedSymbol,
  } = useStockStore()

  // Debounced search with AbortController to prevent race conditions
  useEffect(() => {
    const trimmedQuery = query.trim()

    if (trimmedQuery.length < 1) {
      setResults([])
      setError(null)
      return
    }

    const abortController = new AbortController()

    const timeoutId = setTimeout(async () => {
      setIsLoading(true)
      setError(null)

      try {
        const searchResults = await stockApi.search(trimmedQuery, abortController.signal)
        // Ensure each result has a valid market field
        const normalizedResults = searchResults.map(r => ({
          ...r,
          market: (r.market || 'US') as Market,
        }))
        setResults(normalizedResults)
        setHighlightedIndex(-1)
      } catch (err) {
        // Ignore aborted/cancelled requests
        if (axios.isCancel(err)) {
          return
        }
        setError('Failed to search stocks')
        setResults([])
      } finally {
        if (!abortController.signal.aborted) {
          setIsLoading(false)
        }
      }
    }, DEBOUNCE_MS)

    return () => {
      clearTimeout(timeoutId)
      abortController.abort()
    }
  }, [query])

  // Handle click outside to close dropdown
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Handle keyboard navigation
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      const items = query.trim().length > 0 ? results : recentSearches.map((s) => ({ symbol: s, name: '', market: 'US' as Market, exchange: '' }))
      const itemCount = items.length

      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault()
          setHighlightedIndex((prev) => (prev < itemCount - 1 ? prev + 1 : 0))
          break
        case 'ArrowUp':
          event.preventDefault()
          setHighlightedIndex((prev) => (prev > 0 ? prev - 1 : itemCount - 1))
          break
        case 'Enter':
          event.preventDefault()
          if (highlightedIndex >= 0 && highlightedIndex < itemCount) {
            const item = items[highlightedIndex]
            if (item) {
              handleSelect({
                symbol: item.symbol,
                name: 'name' in item ? item.name : '',
                market: 'market' in item ? item.market : 'US',
                exchange: 'exchange' in item ? item.exchange : '',
              })
            }
          } else if (query.trim().length > 0) {
            // Navigate to the query as a symbol
            handleSelect({
              symbol: query.trim().toUpperCase(),
              name: '',
              market: 'US',
              exchange: '',
            })
          }
          break
        case 'Escape':
          event.preventDefault()
          setIsOpen(false)
          inputRef.current?.blur()
          break
      }
    },
    [query, results, recentSearches, highlightedIndex]
  )

  // Handle selection
  const handleSelect = useCallback(
    (result: SearchResult) => {
      setSelectedSymbol(result.symbol)
      addRecentSearch(result.symbol)
      setQuery('')
      setIsOpen(false)
      setResults([])

      if (onSelect) {
        onSelect(result)
      } else {
        navigate(`/stock/${result.symbol}`)
      }
    },
    [navigate, onSelect, setSelectedSymbol, addRecentSearch]
  )

  // Handle recent search click
  const handleRecentClick = useCallback(
    (symbol: string) => {
      handleSelect({
        symbol,
        name: '',
        market: 'US',
        exchange: '',
      })
    },
    [handleSelect]
  )

  // Clear input
  const handleClear = useCallback(() => {
    setQuery('')
    setResults([])
    setError(null)
    inputRef.current?.focus()
  }, [])

  const showDropdown = isOpen && (
    query.trim().length > 0 ||
    (showRecentSearches && recentSearches.length > 0)
  )

  return (
    <div ref={containerRef} className={cn('relative', className)}>
      {/* Search input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => setIsOpen(true)}
          onKeyDown={handleKeyDown}
          placeholder={defaultPlaceholder}
          autoFocus={autoFocus}
          className="h-10 pl-9 pr-9"
          role="combobox"
          aria-expanded={showDropdown}
          aria-autocomplete="list"
          aria-controls="search-results-listbox"
          aria-activedescendant={highlightedIndex >= 0 ? `search-result-${highlightedIndex}` : undefined}
        />
        {(query.length > 0 || isLoading) && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            {isLoading ? (
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            ) : (
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6"
                onClick={handleClear}
                aria-label="Clear search"
              >
                <X className="h-3 w-3" />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Dropdown */}
      {showDropdown && (
        <div
          id="search-results-listbox"
          role="listbox"
          aria-label="Search results"
          className="absolute top-full z-50 mt-1 w-full rounded-lg border bg-popover shadow-lg"
        >
          <ScrollArea className="max-h-80">
            {/* Search results */}
            {query.trim().length > 0 && (
              <>
                {error ? (
                  <div className="p-4 text-center text-sm text-muted-foreground">
                    {error}
                  </div>
                ) : results.length > 0 ? (
                  <div className="p-1">
                    {results.map((result, index) => (
                      <button
                        key={`${result.symbol}-${result.market}`}
                        type="button"
                        role="option"
                        id={`search-result-${index}`}
                        aria-selected={highlightedIndex === index}
                        onClick={() => handleSelect(result)}
                        onMouseEnter={() => setHighlightedIndex(index)}
                        className={cn(
                          'flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors',
                          highlightedIndex === index
                            ? 'bg-accent text-accent-foreground'
                            : 'hover:bg-accent/50'
                        )}
                      >
                        <div className="flex h-8 w-8 items-center justify-center rounded bg-muted">
                          <TrendingUp className="h-4 w-4 text-muted-foreground" />
                        </div>
                        <div className="flex-1 overflow-hidden">
                          <div className="flex items-center gap-2">
                            <span className="font-medium">{renderSymbol(result, query)}</span>
                            <span
                              className={cn(
                                'rounded px-1.5 py-0.5 text-[10px] font-medium',
                                (marketLabels[result.market.toUpperCase()] ?? defaultMarketLabel).color
                              )}
                            >
                              {(marketLabels[result.market.toUpperCase()] ?? defaultMarketLabel).label}
                            </span>
                          </div>
                          <p className="truncate text-sm text-muted-foreground">
                            {renderName(result, query)}
                          </p>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : !isLoading ? (
                  <div className="p-4 text-center text-sm text-muted-foreground">
                    {t('search.noResults')} "{query}"
                  </div>
                ) : null}
              </>
            )}

            {/* Recent searches */}
            {query.trim().length === 0 && showRecentSearches && recentSearches.length > 0 && (
              <div className="p-1">
                <div className="flex items-center justify-between px-3 py-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    {t('search.recentSearches')}
                  </span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 px-2 text-xs"
                    onClick={(e) => {
                      e.stopPropagation()
                      clearRecentSearches()
                    }}
                  >
                    {t('search.clearRecent')}
                  </Button>
                </div>
                {recentSearches.map((symbol, index) => (
                  <button
                    key={symbol}
                    type="button"
                    role="option"
                    id={`search-result-${index}`}
                    aria-selected={highlightedIndex === index}
                    onClick={() => handleRecentClick(symbol)}
                    onMouseEnter={() => setHighlightedIndex(index)}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors',
                      highlightedIndex === index
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-accent/50'
                    )}
                  >
                    <Clock className="h-4 w-4 text-muted-foreground" />
                    <span className="font-medium">{symbol}</span>
                  </button>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  )
}
