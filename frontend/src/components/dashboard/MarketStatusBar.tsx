import { useTranslation } from 'react-i18next'
import { useMarketStatus, type MarketStatusType, type MarketSession } from './useMarketStatus'
import { cn } from '@/lib/utils'

const DOT_COLORS: Record<MarketStatusType, string> = {
  open: 'bg-green-500',
  closed: 'bg-red-500',
  preMarket: 'bg-amber-500',
  afterHours: 'bg-amber-500',
}

/**
 * Renders a single market item: dot + name + status with countdown.
 */
function MarketItem({ session }: { session: MarketSession }) {
  const { t } = useTranslation('dashboard')

  // Resolve market name using literal keys for type safety
  const marketName = (() => {
    switch (session.id) {
      case 'us': return t('market.us')
      case 'hk': return t('market.hk')
      case 'cn': return t('market.cn')
      case 'metal': return t('market.metal')
    }
  })()

  // Resolve status label using literal keys
  const statusLabel = (() => {
    switch (session.status) {
      case 'open': return t('market.open')
      case 'closed': return t('market.closed')
      case 'preMarket': return t('market.preMarket')
      case 'afterHours': return t('market.afterHours')
    }
  })()

  // Build countdown text if available
  const countdown = (() => {
    if (session.nextEvent == null) return null
    if (session.status === 'open') {
      return t('marketStatus.closesIn', { time: session.nextEvent })
    }
    return t('marketStatus.opensIn', { time: session.nextEvent })
  })()

  const statusText = countdown != null
    ? `${statusLabel} \u00b7 ${countdown}`
    : statusLabel

  return (
    <div className="flex items-center gap-2">
      <span
        className={cn(
          'h-1.5 w-1.5 shrink-0 rounded-full',
          DOT_COLORS[session.status],
        )}
        aria-hidden="true"
      />
      <span className="text-xs font-medium whitespace-nowrap">
        {marketName}
      </span>
      <span className="text-xs text-muted-foreground whitespace-nowrap">
        {statusText}
      </span>
    </div>
  )
}

/**
 * Thin horizontal bar displaying the real-time open/closed status
 * of all four tracked markets (US, HK, CN, Metal).
 *
 * On mobile the bar is horizontally scrollable so all items remain visible
 * without wrapping.
 */
export default function MarketStatusBar() {
  const sessions = useMarketStatus()

  return (
    <div className="rounded-lg bg-muted/30 border px-4 py-2 overflow-x-auto">
      <div className="flex items-center justify-center gap-4 lg:gap-6 min-w-max">
        {sessions.map((session) => (
          <MarketItem key={session.id} session={session} />
        ))}
      </div>
    </div>
  )
}
