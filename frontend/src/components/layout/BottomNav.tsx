import { Link, useLocation } from 'react-router-dom'
import { LayoutDashboard, LineChart, Newspaper, MessageSquare, Briefcase } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

const bottomNavItems = [
  { href: '/', icon: LayoutDashboard, labelKey: 'navigation.dashboard' as const },
  { href: '/watchlist', icon: LineChart, labelKey: 'navigation.watchlist' as const },
  { href: '/news', icon: Newspaper, labelKey: 'navigation.news' as const },
  { href: '/chat', icon: MessageSquare, labelKey: 'navigation.chat' as const },
  { href: '/portfolio', icon: Briefcase, labelKey: 'navigation.portfolio' as const },
] as const

export function BottomNav() {
  const { t } = useTranslation('common')
  const location = useLocation()

  return (
    <nav
      className="fixed bottom-0 left-0 right-0 z-40 border-t bg-card lg:hidden"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      <div className="flex items-stretch">
        {bottomNavItems.map((item) => {
          const isActive =
            item.href === '/'
              ? location.pathname === '/'
              : location.pathname.startsWith(item.href)

          return (
            <Link
              key={item.href}
              to={item.href}
              className={cn(
                'flex flex-1 flex-col items-center justify-center gap-0.5 min-h-[48px] py-1.5 transition-colors',
                isActive ? 'text-primary' : 'text-muted-foreground active:text-primary/70'
              )}
            >
              <item.icon className="h-5 w-5" />
              <span className="text-[10px] font-medium leading-tight">{t(item.labelKey)}</span>
            </Link>
          )
        })}
      </div>
    </nav>
  )
}
