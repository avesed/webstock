import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2, ArrowUpRight, ArrowDownRight } from 'lucide-react'
import { cn, formatCurrency, getPriceChangeColor } from '@/lib/utils'
import type { StockQuote } from '@/types'

interface MarketIndex {
  symbol: string
  name: string
  quote?: StockQuote
}

interface MarketIndicesProps {
  data: MarketIndex[] | undefined
  isLoading: boolean
}

function formatChangePercent(value: number): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export default function MarketIndices({ data, isLoading }: MarketIndicesProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')

  return (
    <div className="h-full rounded-lg border bg-card p-3">
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
        {t('overview')}
      </h3>

      {isLoading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !data || data.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <span className="text-sm text-muted-foreground">
            {t('stock.noData')}
          </span>
        </div>
      ) : (
        <div className="space-y-0.5">
          {data.map((index) => {
            const quote = index.quote
            const changePercent = quote?.changePercent ?? 0

            return (
              <button
                type="button"
                key={index.symbol}
                className="w-full bg-transparent border-0 text-left flex items-center justify-between py-1.5 px-2 rounded hover:bg-accent/50 cursor-pointer transition-colors"
                onClick={() => navigate(`/stock/${index.symbol}`)}
              >
                <div className="flex items-center min-w-0">
                  <span className="text-sm font-medium truncate">
                    {index.symbol}
                  </span>
                  <span className="text-xs text-muted-foreground ml-2 truncate">
                    {index.name}
                  </span>
                </div>

                <div className="flex items-center gap-2 shrink-0 ml-2">
                  {quote && quote.price > 0 ? (
                    <>
                      <span className="text-sm font-mono">
                        {formatCurrency(quote.price)}
                      </span>
                      <span
                        className={cn(
                          'text-sm font-mono inline-flex items-center',
                          getPriceChangeColor(changePercent)
                        )}
                      >
                        {changePercent > 0 ? (
                          <ArrowUpRight className="h-3 w-3 inline" />
                        ) : changePercent < 0 ? (
                          <ArrowDownRight className="h-3 w-3 inline" />
                        ) : null}
                        {formatChangePercent(changePercent)}
                      </span>
                    </>
                  ) : (
                    <span className="text-sm text-muted-foreground">--</span>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
