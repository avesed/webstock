import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { cn, formatCurrency, getPriceChangeColor } from '@/lib/utils'
import { stockApi } from '@/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import type { StockQuote } from '@/types'

interface WatchlistSnapshotProps {
  symbols: string[]
  watchlistName?: string | undefined
}

interface QuoteResult {
  symbol: string
  quote: StockQuote | undefined
}

function formatChangePercent(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function formatChange(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}`
}

export default function WatchlistSnapshot({ symbols, watchlistName }: WatchlistSnapshotProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')
  const isMobile = useIsMobile()

  const { data: quotes, isLoading } = useQuery<QuoteResult[]>({
    queryKey: ['dashboard-watchlist-quotes', symbols.join(',')],
    queryFn: async () => {
      const results = await Promise.all(
        symbols.slice(0, 10).map(async (symbol): Promise<QuoteResult> => {
          try {
            const quote = await stockApi.getQuote(symbol)
            return { symbol, quote }
          } catch (err) {
            console.warn(`[WatchlistSnapshot] Failed to fetch quote for ${symbol}:`, err)
            return { symbol, quote: undefined }
          }
        })
      )
      return results
    },
    enabled: symbols.length > 0,
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
  })

  return (
    <div className="h-full rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {watchlistName ?? t('watchlistSnapshot.title')}
        </h3>
        <button
          type="button"
          className="text-xs text-primary cursor-pointer hover:underline bg-transparent border-none p-0"
          onClick={() => navigate('/watchlist')}
        >
          {t('watchlistSnapshot.viewAll')}
        </button>
      </div>

      {symbols.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-6 gap-1">
          <span className="text-sm text-muted-foreground">
            {t('watchlistSnapshot.empty')}
          </span>
          <button
            type="button"
            className="text-xs text-primary cursor-pointer hover:underline bg-transparent border-none p-0"
            onClick={() => navigate('/watchlist')}
          >
            {t('watchlistSnapshot.addStocks')}
          </button>
        </div>
      ) : isLoading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div>
          {/* Header row - desktop only */}
          {!isMobile && (
            <div className="flex items-center justify-between px-2 pb-1 mb-0.5">
              <span className="text-[10px] text-muted-foreground uppercase tracking-wider w-20">
                {t('watchlistSnapshot.colSymbol')}
              </span>
              <div className="flex items-center gap-4">
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider w-20 text-right">
                  {t('watchlistSnapshot.colPrice')}
                </span>
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider w-20 text-right">
                  {t('watchlistSnapshot.colChange')}
                </span>
                <span className="text-[10px] text-muted-foreground uppercase tracking-wider w-20 text-right">
                  {t('watchlistSnapshot.colChangePercent')}
                </span>
              </div>
            </div>
          )}

          <div className="space-y-0.5">
            {quotes?.map(({ symbol, quote }) => (
                <button
                  type="button"
                  key={symbol}
                  className="w-full flex items-center justify-between py-1.5 px-2 rounded cursor-pointer hover:bg-accent/50 transition-colors bg-transparent border-0"
                  onClick={() => navigate(`/stock/${symbol}`)}
                >
                  <span className="text-sm font-medium w-20 truncate text-left">
                    {symbol}
                  </span>

                  <div className="flex items-center gap-4">
                    <span className="text-sm font-mono w-20 text-right">
                      {quote ? formatCurrency(quote.price) : '--'}
                    </span>

                    {!isMobile && (
                      <span
                        className={cn(
                          'text-sm font-mono w-20 text-right',
                          quote ? getPriceChangeColor(quote.change) : ''
                        )}
                      >
                        {quote ? formatChange(quote.change) : '--'}
                      </span>
                    )}

                    <span
                      className={cn(
                        'text-sm font-mono w-20 text-right',
                        quote ? getPriceChangeColor(quote.changePercent) : ''
                      )}
                    >
                      {quote ? formatChangePercent(quote.changePercent) : '--'}
                    </span>
                  </div>
                </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
