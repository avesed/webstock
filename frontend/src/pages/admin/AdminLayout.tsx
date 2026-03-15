import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import { Suspense, useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowLeft, ChevronLeft, ChevronRight, ChevronDown, Menu } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ErrorBoundary } from '@/components/layout/ErrorBoundary'
import { adminRouteGroups } from './adminRoutes'
import type { AdminRouteItem } from './adminRoutes'

const SIDEBAR_KEY = 'webstock-admin-sidebar-collapsed'
const EXPANDED_KEY = 'webstock-admin-expanded-items'

function getStoredCollapsed(): boolean {
  if (typeof window === 'undefined') return false
  return localStorage.getItem(SIDEBAR_KEY) === 'true'
}

function getStoredExpanded(): string[] {
  if (typeof window === 'undefined') return []
  try {
    const raw = localStorage.getItem(EXPANDED_KEY)
    return raw ? JSON.parse(raw) as string[] : []
  } catch {
    return []
  }
}

/** Detect which parent items should be auto-expanded based on current URL */
function getAutoExpanded(pathname: string): string[] {
  const relativePath = pathname.replace(/^\/admin\/?/, '')
  const expanded: string[] = []
  for (const group of adminRouteGroups) {
    for (const item of group.items) {
      if (item.children && relativePath.startsWith(item.path + '/')) {
        expanded.push(item.path)
      }
    }
  }
  return expanded
}

export default function AdminLayout() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const location = useLocation()
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(getStoredCollapsed)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [expandedItems, setExpandedItems] = useState<Set<string>>(() => {
    const stored = getStoredExpanded()
    const auto = getAutoExpanded(location.pathname)
    return new Set([...stored, ...auto])
  })

  // Close mobile sidebar on route change
  useEffect(() => {
    setMobileOpen(false)
  }, [location.pathname])

  // Auto-expand parent when navigating to a child route
  useEffect(() => {
    const auto = getAutoExpanded(location.pathname)
    if (auto.length > 0) {
      setExpandedItems((prev) => {
        const next = new Set(prev)
        let changed = false
        for (const key of auto) {
          if (!next.has(key)) {
            next.add(key)
            changed = true
          }
        }
        return changed ? next : prev
      })
    }
  }, [location.pathname])

  const toggleCollapsed = () => {
    const next = !collapsed
    setCollapsed(next)
    localStorage.setItem(SIDEBAR_KEY, String(next))
  }

  const toggleExpanded = useCallback((path: string) => {
    setExpandedItems((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      localStorage.setItem(EXPANDED_KEY, JSON.stringify([...next]))
      return next
    })
  }, [])

  // Determine active path segments
  const relativePath = location.pathname.replace(/^\/admin\/?/, '')
  const activeSegment = relativePath.split('/')[0] || 'users'
  const activeChildSegment = relativePath.split('/')[1] || ''

  const renderNavItem = (item: AdminRouteItem) => {
    const hasChildren = item.children && item.children.length > 0
    const isExpanded = expandedItems.has(item.path)
    const isParentActive = activeSegment === item.path
    const label = t(item.labelKey as never) as string

    // ── Item with children (expandable) ──
    if (hasChildren) {
      // Collapsed sidebar: show icon only, click navigates to first child
      if (collapsed) {
        const firstChild = item.children![0]!
        return (
          <Tooltip key={item.path}>
            <TooltipTrigger asChild>
              <Link
                to={`/admin/${item.path}/${firstChild.path}`}
                className={cn(
                  'flex items-center justify-center rounded-lg px-2 py-2 text-sm font-medium transition-colors',
                  isParentActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                )}
              >
                <item.icon className="h-4 w-4 shrink-0" />
              </Link>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={8}>
              {label}
            </TooltipContent>
          </Tooltip>
        )
      }

      // Expanded sidebar: parent toggle + child links
      return (
        <div key={item.path}>
          <button
            type="button"
            onClick={() => toggleExpanded(item.path)}
            className={cn(
              'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
              isParentActive
                ? 'text-primary'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
            )}
          >
            <item.icon className="h-4 w-4 shrink-0" />
            <span className="flex-1 truncate text-left">{label}</span>
            {isExpanded ? (
              <ChevronDown className="h-3.5 w-3.5 shrink-0 opacity-60" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 shrink-0 opacity-60" />
            )}
          </button>

          {/* Children */}
          {isExpanded && (
            <div className="ml-4 mt-0.5 space-y-0.5 border-l border-border/50 pl-2">
              {item.children!.map((child) => {
                const childFullPath = `${item.path}/${child.path}`
                const isChildActive = isParentActive && activeChildSegment === child.path
                const childLabel = t(child.labelKey as never) as string

                return (
                  <Link
                    key={child.path}
                    to={`/admin/${childFullPath}`}
                    onClick={() => setMobileOpen(false)}
                    className={cn(
                      'flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs font-medium transition-colors',
                      isChildActive
                        ? 'bg-primary/10 text-primary'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                    )}
                  >
                    <child.icon className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{childLabel}</span>
                  </Link>
                )
              })}
            </div>
          )}
        </div>
      )
    }

    // ── Regular item (no children) ──
    const isActive = activeSegment === item.path

    const linkContent = (
      <Link
        to={`/admin/${item.path}`}
        onClick={() => setMobileOpen(false)}
        className={cn(
          'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
          collapsed && 'justify-center px-2',
          isActive
            ? 'bg-primary/10 text-primary'
            : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
        )}
      >
        <item.icon className="h-4 w-4 shrink-0" />
        {!collapsed && <span className="truncate">{label}</span>}
      </Link>
    )

    if (collapsed) {
      return (
        <Tooltip key={item.path}>
          <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
          <TooltipContent side="right" sideOffset={8}>
            {label}
          </TooltipContent>
        </Tooltip>
      )
    }

    return <div key={item.path}>{linkContent}</div>
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div className="flex h-screen overflow-hidden">
        {/* Mobile overlay */}
        {mobileOpen && (
          <div
            className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          className={cn(
            'fixed inset-y-0 left-0 z-50 flex flex-col border-r bg-card transition-all duration-300 lg:static',
            collapsed ? 'w-14' : 'w-60',
            mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
          )}
        >
          {/* Header: back link + title */}
          <div
            className={cn(
              'flex h-14 items-center border-b shrink-0',
              collapsed ? 'justify-center px-2' : 'gap-2 px-4'
            )}
          >
            {collapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8"
                    onClick={() => navigate('/')}
                  >
                    <ArrowLeft className="h-4 w-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">{t('sidebar.backToApp')}</TooltipContent>
              </Tooltip>
            ) : (
              <>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 shrink-0"
                  onClick={() => navigate('/')}
                >
                  <ArrowLeft className="h-4 w-4" />
                </Button>
                <span className="text-sm font-semibold truncate">{t('title')}</span>
              </>
            )}
          </div>

          {/* Navigation groups */}
          <ScrollArea className="flex-1 py-3">
            <nav className="space-y-4">
              {adminRouteGroups.map((group) => (
                <div key={group.groupKey}>
                  {/* Group label */}
                  {!collapsed && (
                    <div className="px-4 pb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      {t(group.groupKey as never)}
                    </div>
                  )}
                  <div className="space-y-0.5 px-2">
                    {group.items.map(renderNavItem)}
                  </div>
                </div>
              ))}
            </nav>
          </ScrollArea>

          {/* Collapse toggle (desktop only) */}
          <div className="hidden border-t p-2 lg:block">
            <Button
              variant="ghost"
              size="sm"
              className={cn('w-full', collapsed ? 'px-2' : 'justify-start')}
              onClick={toggleCollapsed}
            >
              {collapsed ? (
                <ChevronRight className="h-4 w-4" />
              ) : (
                <>
                  <ChevronLeft className="mr-2 h-4 w-4" />
                  {tCommon('layout.collapse')}
                </>
              )}
            </Button>
          </div>
        </aside>

        {/* Content area */}
        <div className="flex flex-1 flex-col min-h-0 min-w-0 overflow-hidden">
          {/* Mobile header */}
          <div className="flex h-14 items-center gap-3 border-b bg-card px-4 shrink-0 lg:hidden">
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0"
              onClick={() => setMobileOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </Button>
            <span className="text-sm font-semibold truncate">{t('title')}</span>
          </div>

          {/* Page content */}
          <main className="flex-1 overflow-y-auto overflow-x-hidden p-4 lg:p-6">
            <ErrorBoundary key={location.pathname}>
              <Suspense
                fallback={
                  <div className="flex h-full items-center justify-center">
                    <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
                  </div>
                }
              >
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}
