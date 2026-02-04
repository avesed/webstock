import { useState, useEffect, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
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

const DEBOUNCE_MS = 300

// Market flag/label mapping
const marketLabels: Record<Market, { label: string; color: string }> = {
  US: { label: 'US', color: 'bg-blue-500/10 text-blue-600 dark:text-blue-400' },
  HK: { label: 'HK', color: 'bg-red-500/10 text-red-600 dark:text-red-400' },
  CN: { label: 'CN', color: 'bg-amber-500/10 text-amber-600 dark:text-amber-400' },
}

export default function StockSearch({
  placeholder = 'Search stocks...',
  className,
  onSelect,
  autoFocus = false,
  showRecentSearches = true,
}: StockSearchProps) {
  const navigate = useNavigate()
  const inputRef = useRef<HTMLInputElement>(null)
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

  // Debounced search
  useEffect(() => {
    const trimmedQuery = query.trim()

    if (trimmedQuery.length < 1) {
      setResults([])
      setError(null)
      return
    }

    const timeoutId = setTimeout(async () => {
      setIsLoading(true)
      setError(null)

      try {
        const searchResults = await stockApi.search(trimmedQuery)
        setResults(searchResults)
        setHighlightedIndex(-1)
      } catch {
        setError('Failed to search stocks')
        setResults([])
      } finally {
        setIsLoading(false)
      }
    }, DEBOUNCE_MS)

    return () => clearTimeout(timeoutId)
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
      const items = query.trim().length > 0 ? results : recentSearches.map((s) => ({ symbol: s, name: '', market: 'US' as Market, type: '' }))
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
                type: 'type' in item ? item.type : '',
              })
            }
          } else if (query.trim().length > 0) {
            // Navigate to the query as a symbol
            handleSelect({
              symbol: query.trim().toUpperCase(),
              name: '',
              market: 'US',
              type: '',
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
        type: '',
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
          placeholder={placeholder}
          autoFocus={autoFocus}
          className="h-10 pl-9 pr-9"
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
              >
                <X className="h-3 w-3" />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Dropdown */}
      {showDropdown && (
        <div className="absolute top-full z-50 mt-1 w-full rounded-lg border bg-popover shadow-lg">
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
                            <span className="font-medium">{result.symbol}</span>
                            <span
                              className={cn(
                                'rounded px-1.5 py-0.5 text-[10px] font-medium',
                                marketLabels[result.market].color
                              )}
                            >
                              {marketLabels[result.market].label}
                            </span>
                          </div>
                          <p className="truncate text-sm text-muted-foreground">
                            {result.name || result.type}
                          </p>
                        </div>
                      </button>
                    ))}
                  </div>
                ) : !isLoading ? (
                  <div className="p-4 text-center text-sm text-muted-foreground">
                    No results found for "{query}"
                  </div>
                ) : null}
              </>
            )}

            {/* Recent searches */}
            {query.trim().length === 0 && showRecentSearches && recentSearches.length > 0 && (
              <div className="p-1">
                <div className="flex items-center justify-between px-3 py-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    Recent Searches
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
                    Clear all
                  </Button>
                </div>
                {recentSearches.map((symbol, index) => (
                  <button
                    key={symbol}
                    type="button"
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
