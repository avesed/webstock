import { formatCurrency, formatCompactNumber } from '@/lib/utils'
import type { StockQuote } from '@/types'

interface StockStatsGridProps {
  quote: StockQuote
}

/**
 * Stats grid component for displaying stock statistics.
 * Shows key metrics like open, close, high, low, volume, and market cap.
 */
export function StockStatsGrid({ quote }: StockStatsGridProps) {
  const stats = [
    { label: 'Open', value: quote.open != null ? formatCurrency(quote.open) : 'N/A' },
    { label: 'Previous Close', value: quote.previousClose != null ? formatCurrency(quote.previousClose) : 'N/A' },
    { label: 'Day High', value: quote.dayHigh != null ? formatCurrency(quote.dayHigh) : 'N/A' },
    { label: 'Day Low', value: quote.dayLow != null ? formatCurrency(quote.dayLow) : 'N/A' },
    { label: 'Volume', value: quote.volume != null ? formatCompactNumber(quote.volume) : 'N/A' },
    {
      label: 'Market Cap',
      value: quote.marketCap ? formatCompactNumber(quote.marketCap) : 'N/A',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-4">
      {stats.map((stat) => (
        <div key={stat.label}>
          <p className="text-sm text-muted-foreground">{stat.label}</p>
          <p className="font-medium">{stat.value}</p>
        </div>
      ))}
    </div>
  )
}
