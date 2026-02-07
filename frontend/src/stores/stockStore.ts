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

  // Recent searches (per-user isolated)
  recentSearches: string[]
  currentUserId: string | null
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
  // User session management for recent searches isolation
  loadUserRecentSearches: (userId: string) => void
  clearUserSession: () => void
}

type StockStore = StockState & StockActions

const MAX_RECENT_SEARCHES = 10
const RECENT_SEARCHES_KEY_PREFIX = 'webstock-recent-searches'

function getRecentSearchesKey(userId: string): string {
  return `${RECENT_SEARCHES_KEY_PREFIX}-${userId}`
}

function getStoredRecentSearches(userId: string | null): string[] {
  if (typeof window === 'undefined' || userId === null) return []
  try {
    const stored = localStorage.getItem(getRecentSearchesKey(userId))
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

function saveRecentSearches(userId: string | null, searches: string[]): void {
  if (typeof window === 'undefined' || userId === null) return
  localStorage.setItem(getRecentSearchesKey(userId), JSON.stringify(searches))
}

export const useStockStore = create<StockStore>((set, get) => ({
  // State
  selectedSymbol: null,
  selectedQuote: null,
  selectedInfo: null,
  chartTimeframe: '1M',
  searchQuery: '',
  isSearching: false,
  recentSearches: [],
  currentUserId: null,

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
    const { recentSearches, currentUserId } = get()
    const upperSymbol = symbol.toUpperCase()

    // Remove if already exists
    const filtered = recentSearches.filter((s) => s !== upperSymbol)

    // Add to front
    const updated = [upperSymbol, ...filtered].slice(0, MAX_RECENT_SEARCHES)

    saveRecentSearches(currentUserId, updated)
    set({ recentSearches: updated })
  },

  clearRecentSearches: () => {
    const { currentUserId } = get()
    saveRecentSearches(currentUserId, [])
    set({ recentSearches: [] })
  },

  loadUserRecentSearches: (userId: string) => {
    const recentSearches = getStoredRecentSearches(userId)
    set({ currentUserId: userId, recentSearches })
  },

  clearUserSession: () => {
    set({ currentUserId: null, recentSearches: [] })
  },
}))
