import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { stockApi, watchlistApi, alertsApi, portfolioApi, newsApi } from '@/api'
import { useAuthStore } from '@/stores/authStore'
import type { StockQuote } from '@/types'
import {
  MarketStatusBar,
  StatsStrip,
  MarketIndices,
  WatchlistSnapshot,
  PortfolioMiniChart,
  CompactNewsList,
} from '@/components/dashboard'

const EMPTY_SYMBOLS: string[] = []

interface MarketIndex {
  symbol: string
  name: string
  quote?: StockQuote
}

const MARKET_INDICES: MarketIndex[] = [
  { symbol: 'SPY', name: 'S&P 500' },
  { symbol: 'QQQ', name: 'NASDAQ' },
  { symbol: 'DIA', name: 'Dow Jones' },
]

export default function DashboardPage() {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuthStore()

  // Only fetch data when auth is ready to avoid 401 race conditions
  const canFetch = isAuthenticated && !isAuthLoading

  // ---- Shared data queries ----

  const { data: watchlists } = useQuery({
    queryKey: ['watchlists'],
    queryFn: watchlistApi.getAll,
    enabled: canFetch,
  })

  const { data: alerts } = useQuery({
    queryKey: ['alerts'],
    queryFn: alertsApi.getAll,
    enabled: canFetch,
  })

  const { data: portfolios } = useQuery({
    queryKey: ['portfolios'],
    queryFn: portfolioApi.getAll,
    enabled: canFetch,
  })

  const { data: marketQuotes, isLoading: isLoadingMarket } = useQuery({
    queryKey: ['market-indices'],
    queryFn: async () => {
      const quotes = await Promise.all(
        MARKET_INDICES.map(async (index) => {
          try {
            const quote = await stockApi.getQuote(index.symbol)
            return { ...index, quote }
          } catch (err) {
            console.warn(`[Dashboard] Failed to fetch quote for ${index.symbol}:`, err)
            return index
          }
        })
      )
      return quotes
    },
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
    enabled: canFetch,
  })

  const { data: newsData, isLoading: isLoadingNews } = useQuery({
    queryKey: ['news-dashboard'],
    queryFn: async () => {
      const res = await newsApi.getMarket(1, 10)
      return res.items
    },
    enabled: canFetch,
  })

  // ---- Derived data ----

  // Identify default watchlist from list response (which only has metadata, not items)
  const defaultWatchlistMeta = useMemo(() => {
    if (!watchlists || watchlists.length === 0) return undefined
    return watchlists.find((w) => w.isDefault) ?? watchlists[0]
  }, [watchlists])

  // Fetch the default watchlist's detail (which includes items with symbols)
  const { data: defaultWatchlistDetail } = useQuery({
    queryKey: ['watchlist-detail', defaultWatchlistMeta?.id],
    queryFn: () => watchlistApi.get(defaultWatchlistMeta!.id),
    enabled: canFetch && defaultWatchlistMeta?.id != null,
  })

  const defaultWatchlistSymbols = defaultWatchlistDetail?.symbols ?? EMPTY_SYMBOLS
  const defaultWatchlistName = defaultWatchlistDetail?.name ?? defaultWatchlistMeta?.name

  // ---- Layout ----

  return (
    <div className="space-y-1.5">
      <MarketStatusBar />

      <StatsStrip
        portfolios={portfolios}
        watchlists={watchlists}
        alerts={alerts}
      />

      <div className="grid gap-1.5 lg:grid-cols-5">
        <div className="min-w-0 lg:col-span-2">
          <MarketIndices data={marketQuotes} isLoading={isLoadingMarket} />
        </div>
        <div className="min-w-0 lg:col-span-3">
          <WatchlistSnapshot
            symbols={defaultWatchlistSymbols}
            watchlistName={defaultWatchlistName}
          />
        </div>
      </div>

      <div className="grid gap-1.5 lg:grid-cols-5">
        <div className="min-w-0 lg:col-span-2">
          <PortfolioMiniChart portfolios={portfolios} />
        </div>
        <div className="min-w-0 lg:col-span-3">
          <CompactNewsList newsData={newsData} isLoading={isLoadingNews} />
        </div>
      </div>
    </div>
  )
}
