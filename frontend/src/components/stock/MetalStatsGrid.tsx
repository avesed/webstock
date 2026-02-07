import { formatCurrency } from '@/lib/utils'
import type { StockQuote } from '@/types'

interface MetalStatsGridProps {
  quote: StockQuote
  symbol: string
}

/**
 * Stats grid component for precious metals.
 * Shows exchange, unit, and price statistics specific to commodities.
 */
export function MetalStatsGrid({ quote, symbol }: MetalStatsGridProps) {
  // Determine exchange based on symbol
  const getExchange = (sym: string): string => {
    const upperSym = sym.toUpperCase()
    if (upperSym.includes('GC') || upperSym.includes('SI')) {
      return 'COMEX'
    }
    return 'NYMEX'
  }

  const stats = [
    { label: 'Exchange', value: quote.market === 'METAL' ? getExchange(symbol) : (quote.market || 'COMEX') },
    { label: 'Unit', value: 'troy oz' },
    { label: 'Day High', value: quote.dayHigh != null ? formatCurrency(quote.dayHigh) : 'N/A' },
    { label: 'Day Low', value: quote.dayLow != null ? formatCurrency(quote.dayLow) : 'N/A' },
    { label: 'Open', value: quote.open != null ? formatCurrency(quote.open) : 'N/A' },
    { label: 'Previous Close', value: quote.previousClose != null ? formatCurrency(quote.previousClose) : 'N/A' },
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
