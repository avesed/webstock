import { formatCurrency, formatCompactNumber, formatPercent } from '@/lib/utils'
import type { StockFinancials } from '@/types'

interface FinancialsGridProps {
  financials: StockFinancials
}

/**
 * Financials grid component for displaying company financial metrics.
 * Shows P/E ratio, P/B ratio, EPS, revenue, margins, and other key metrics.
 */
export function FinancialsGrid({ financials }: FinancialsGridProps) {
  const metrics = [
    {
      label: 'P/E Ratio',
      value: financials.peRatio?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'P/B Ratio',
      value: financials.pbRatio?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'EPS',
      value: financials.eps ? formatCurrency(financials.eps) : 'N/A',
    },
    {
      label: 'EPS Growth',
      value: financials.epsGrowth ? formatPercent(financials.epsGrowth) : 'N/A',
    },
    {
      label: 'Revenue',
      value: financials.revenue ? formatCompactNumber(financials.revenue) : 'N/A',
    },
    {
      label: 'Revenue Growth',
      value: financials.revenueGrowth ? formatPercent(financials.revenueGrowth) : 'N/A',
    },
    {
      label: 'Net Income',
      value: financials.netIncome ? formatCompactNumber(financials.netIncome) : 'N/A',
    },
    {
      label: 'Net Margin',
      value: financials.netMargin ? formatPercent(financials.netMargin) : 'N/A',
    },
    {
      label: 'ROE',
      value: financials.roe ? formatPercent(financials.roe) : 'N/A',
    },
    {
      label: 'ROA',
      value: financials.roa ? formatPercent(financials.roa) : 'N/A',
    },
    {
      label: 'Debt/Equity',
      value: financials.debtToEquity?.toFixed(2) ?? 'N/A',
    },
    {
      label: 'Dividend Yield',
      value: financials.dividendYield ? formatPercent(financials.dividendYield) : 'N/A',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
      {metrics.map((metric) => (
        <div key={metric.label} className="space-y-1">
          <p className="text-sm text-muted-foreground">{metric.label}</p>
          <p className="font-medium">{metric.value}</p>
        </div>
      ))}
    </div>
  )
}
