import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import type { Portfolio, Watchlist, Alert } from '@/types'
import { formatCurrency, cn, getPriceChangeColor } from '@/lib/utils'

interface StatsStripProps {
  portfolios: Portfolio[] | undefined
  watchlists: Watchlist[] | undefined
  alerts: Alert[] | undefined
}

interface StatCellProps {
  label: string
  value: string
  subText: string
  subTextClassName?: string
  onClick: () => void
  /** Whether to show a right border (desktop divider) */
  showDivider: boolean
}

function StatCell({ label, value, subText, subTextClassName, onClick, showDivider }: StatCellProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'px-4 py-3 cursor-pointer hover:bg-accent/50 transition-colors text-left',
        showDivider && 'lg:border-r lg:border-border',
      )}
    >
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-lg font-mono font-semibold">{value}</p>
      <p className={cn('text-xs text-muted-foreground', subTextClassName)}>
        {subText}
      </p>
    </button>
  )
}

/**
 * Horizontal inline strip of 4 key portfolio/watchlist/alert statistics.
 *
 * Desktop: single row with vertical dividers between cells.
 * Mobile: 2x2 grid layout.
 */
export default function StatsStrip({ portfolios, watchlists, alerts }: StatsStripProps) {
  const { t } = useTranslation('dashboard')
  const navigate = useNavigate()

  // Derived stats
  const totalPortfolioValue = portfolios?.reduce(
    (total, p) => total + (p.totalValue || 0),
    0,
  ) ?? 0

  const totalPortfolioChange = portfolios?.reduce(
    (total, p) => total + (p.totalGain || 0),
    0,
  ) ?? 0

  const totalWatchlistStocks = watchlists?.reduce(
    (total, w) => total + (w.itemCount ?? w.symbols?.length ?? 0),
    0,
  ) ?? 0

  const activeAlertsCount = alerts?.filter((a) => a.status === 'ACTIVE').length ?? 0

  // Formatting helpers
  const changeSign = totalPortfolioChange >= 0 ? '+' : ''
  const changeColor = getPriceChangeColor(totalPortfolioChange)

  return (
    <div className="rounded-lg border bg-card p-0 grid grid-cols-2 lg:grid-cols-4">
      {/* Cell 1: Portfolio Value */}
      <StatCell
        label={t('stats.portfolioValue')}
        value={formatCurrency(totalPortfolioValue)}
        subText={`${changeSign}${formatCurrency(totalPortfolioChange)}`}
        subTextClassName={changeColor}
        onClick={() => navigate('/portfolio')}
        showDivider
      />

      {/* Cell 2: Total P&L */}
      <StatCell
        label={t('portfolio.totalGain')}
        value={`${changeSign}${formatCurrency(totalPortfolioChange)}`}
        subText={`${portfolios?.length ?? 0} ${t('portfolio.title').toLowerCase()}`}
        subTextClassName={changeColor}
        onClick={() => navigate('/portfolio')}
        showDivider
      />

      {/* Cell 3: Watching */}
      <StatCell
        label={t('stats.watching')}
        value={String(totalWatchlistStocks)}
        subText={`${watchlists?.length ?? 0} ${t('watchlist.myWatchlists').toLowerCase()}`}
        onClick={() => navigate('/watchlist')}
        showDivider
      />

      {/* Cell 4: Alerts */}
      <StatCell
        label={t('stats.alerts')}
        value={String(activeAlertsCount)}
        subText={`${alerts?.length ?? 0} ${t('alerts.title').toLowerCase()}`}
        onClick={() => navigate('/alerts')}
        showDivider={false}
      />
    </div>
  )
}
