import { create } from 'zustand'
import type { StockQuote, StockInfo, ChartTimeframe } from '@/types'

interface StockState {
  // Currently selected stock
  selectedSymbol: string | null
  selectedQuote: StockQuote | null
  selectedInfo: StockInfo | null

  // Chart settings
  chartTimeframe: ChartTimeframe

  // Search state
  searchQuery: string
  isSearching: boolean

  // Recent searches
  recentSearches: string[]
}

interface StockActions {
  setSelectedSymbol: (symbol: string | null) => void
  setSelectedQuote: (quote: StockQuote | null) => void
  setSelectedInfo: (info: StockInfo | null) => void
  setChartTimeframe: (timeframe: ChartTimeframe) => void
  setSearchQuery: (query: string) => void
  setIsSearching: (isSearching: boolean) => void
  addRecentSearch: (symbol: string) => void
  clearRecentSearches: () => void
}

type StockStore = StockState & StockActions

const MAX_RECENT_SEARCHES = 10
const RECENT_SEARCHES_KEY = 'webstock-recent-searches'

function getStoredRecentSearches(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const stored = localStorage.getItem(RECENT_SEARCHES_KEY)
    if (stored) {
      const parsed: unknown = JSON.parse(stored)
      if (Array.isArray(parsed)) {
        return parsed.filter((item): item is string => typeof item === 'string')
      }
    }
  } catch {
    // Ignore parse errors
  }
  return []
}

function saveRecentSearches(searches: string[]): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(RECENT_SEARCHES_KEY, JSON.stringify(searches))
}

export const useStockStore = create<StockStore>((set, get) => ({
  // State
  selectedSymbol: null,
  selectedQuote: null,
  selectedInfo: null,
  chartTimeframe: '1M',
  searchQuery: '',
  isSearching: false,
  recentSearches: getStoredRecentSearches(),

  // Actions
  setSelectedSymbol: (symbol: string | null) => {
    set({ selectedSymbol: symbol })
    if (symbol) {
      get().addRecentSearch(symbol)
    }
  },

  setSelectedQuote: (quote: StockQuote | null) => {
    set({ selectedQuote: quote })
  },

  setSelectedInfo: (info: StockInfo | null) => {
    set({ selectedInfo: info })
  },

  setChartTimeframe: (timeframe: ChartTimeframe) => {
    set({ chartTimeframe: timeframe })
  },

  setSearchQuery: (query: string) => {
    set({ searchQuery: query })
  },

  setIsSearching: (isSearching: boolean) => {
    set({ isSearching: isSearching })
  },

  addRecentSearch: (symbol: string) => {
    const { recentSearches } = get()
    const upperSymbol = symbol.toUpperCase()

    // Remove if already exists
    const filtered = recentSearches.filter((s) => s !== upperSymbol)

    // Add to front
    const updated = [upperSymbol, ...filtered].slice(0, MAX_RECENT_SEARCHES)

    saveRecentSearches(updated)
    set({ recentSearches: updated })
  },

  clearRecentSearches: () => {
    saveRecentSearches([])
    set({ recentSearches: [] })
  },
}))
