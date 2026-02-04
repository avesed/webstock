import { Outlet, Link, useLocation, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  LineChart,
  Briefcase,
  Bell,
  FileText,
  Newspaper,
  MessageSquare,
  Settings,
  LogOut,
  Moon,
  Sun,
  Monitor,
  Menu,
  ChevronLeft,
  ChevronRight,
  Home,
} from 'lucide-react'
import { useState, useEffect } from 'react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { StockSearch } from '@/components/search'
import type { Theme } from '@/types'

const SIDEBAR_COLLAPSED_KEY = 'webstock-sidebar-collapsed'

interface NavItem {
  href: string
  icon: React.ElementType
  label: string
  match?: RegExp
}

const navItems: NavItem[] = [
  { href: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { href: '/watchlist', icon: LineChart, label: 'Watchlist' },
  { href: '/portfolio', icon: Briefcase, label: 'Portfolio' },
  { href: '/alerts', icon: Bell, label: 'Alerts' },
  { href: '/reports', icon: FileText, label: 'Reports' },
  { href: '/news', icon: Newspaper, label: 'News' },
  { href: '/chat', icon: MessageSquare, label: 'AI Chat' },
]

const themeOptions: { value: Theme; label: string; icon: React.ElementType }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

// Breadcrumb configuration
interface BreadcrumbConfig {
  path: string
  label: string
  match?: RegExp
  dynamic?: boolean
}

const breadcrumbConfig: BreadcrumbConfig[] = [
  { path: '/', label: 'Dashboard' },
  { path: '/watchlist', label: 'Watchlist' },
  { path: '/portfolio', label: 'Portfolio' },
  { path: '/alerts', label: 'Alerts' },
  { path: '/reports', label: 'Reports' },
  { path: '/news', label: 'News' },
  { path: '/chat', label: 'AI Chat' },
  { path: '/stock', label: 'Stock', match: /^\/stock\//, dynamic: true },
]

function getBreadcrumbs(pathname: string): { label: string; href: string }[] {
  const breadcrumbs: { label: string; href: string }[] = []

  // Always add home
  if (pathname !== '/') {
    breadcrumbs.push({ label: 'Home', href: '/' })
  }

  // Find matching config
  for (const config of breadcrumbConfig) {
    if (config.match ? config.match.test(pathname) : pathname === config.path) {
      if (config.dynamic) {
        // For dynamic routes like /stock/:symbol
        const parts = pathname.split('/')
        const symbol = parts[2]
        if (symbol) {
          breadcrumbs.push({ label: symbol.toUpperCase(), href: pathname })
        }
      } else if (pathname !== '/') {
        breadcrumbs.push({ label: config.label, href: config.path })
      }
      break
    }
  }

  return breadcrumbs
}

function getStoredSidebarState(): boolean {
  if (typeof window === 'undefined') return false
  const stored = localStorage.getItem(SIDEBAR_COLLAPSED_KEY)
  return stored === 'true'
}

function storeSidebarState(collapsed: boolean): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(collapsed))
}

export default function MainLayout() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuthStore()
  const { theme, setTheme } = useThemeStore()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(getStoredSidebarState)

  // Initialize sidebar state from localStorage
  useEffect(() => {
    setSidebarCollapsed(getStoredSidebarState())
  }, [])

  // Close mobile menu on route change
  useEffect(() => {
    setMobileMenuOpen(false)
  }, [location.pathname])

  const handleLogout = async () => {
    await logout()
  }

  const toggleSidebar = () => {
    const newState = !sidebarCollapsed
    setSidebarCollapsed(newState)
    storeSidebarState(newState)
  }

  const currentThemeOption = themeOptions.find((opt) => opt.value === theme)
  const ThemeIcon = currentThemeOption?.icon ?? Monitor
  const breadcrumbs = getBreadcrumbs(location.pathname)

  return (
    <TooltipProvider delayDuration={0}>
      <div className="flex min-h-screen bg-background">
        {/* Mobile sidebar overlay */}
        {mobileMenuOpen && (
          <div
            className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm lg:hidden"
            onClick={() => setMobileMenuOpen(false)}
          />
        )}

        {/* Sidebar */}
        <aside
          className={cn(
            'fixed inset-y-0 left-0 z-50 flex flex-col border-r bg-card transition-all duration-300 lg:static',
            sidebarCollapsed ? 'w-16' : 'w-64',
            mobileMenuOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
          )}
        >
          {/* Logo */}
          <div
            className={cn(
              'flex h-16 items-center border-b px-4',
              sidebarCollapsed ? 'justify-center' : 'gap-2 px-6'
            )}
          >
            <LineChart className="h-6 w-6 shrink-0 text-primary" />
            {!sidebarCollapsed && (
              <span className="text-xl font-bold">WebStock</span>
            )}
          </div>

          {/* Navigation */}
          <ScrollArea className="flex-1 px-2 py-4">
            <nav className="space-y-1">
              {navItems.map((item) => {
                const isActive =
                  item.href === '/'
                    ? location.pathname === '/'
                    : location.pathname.startsWith(item.href)

                const linkContent = (
                  <Link
                    to={item.href}
                    onClick={() => setMobileMenuOpen(false)}
                    className={cn(
                      'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                      sidebarCollapsed && 'justify-center px-2',
                      isActive
                        ? 'bg-primary text-primary-foreground'
                        : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
                    )}
                  >
                    <item.icon className="h-5 w-5 shrink-0" />
                    {!sidebarCollapsed && item.label}
                  </Link>
                )

                if (sidebarCollapsed) {
                  return (
                    <Tooltip key={item.href}>
                      <TooltipTrigger asChild>{linkContent}</TooltipTrigger>
                      <TooltipContent side="right" sideOffset={8}>
                        {item.label}
                      </TooltipContent>
                    </Tooltip>
                  )
                }

                return <div key={item.href}>{linkContent}</div>
              })}
            </nav>
          </ScrollArea>

          {/* Collapse button (desktop only) */}
          <div className="hidden border-t p-2 lg:block">
            <Button
              variant="ghost"
              size="sm"
              className={cn('w-full', sidebarCollapsed ? 'px-2' : 'justify-start')}
              onClick={toggleSidebar}
            >
              {sidebarCollapsed ? (
                <ChevronRight className="h-4 w-4" />
              ) : (
                <>
                  <ChevronLeft className="mr-2 h-4 w-4" />
                  Collapse
                </>
              )}
            </Button>
          </div>

          {/* User section */}
          <div className={cn('border-t p-4', sidebarCollapsed && 'p-2')}>
            {sidebarCollapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <div className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-primary-foreground mx-auto cursor-default">
                    {user?.email.charAt(0).toUpperCase()}
                  </div>
                </TooltipTrigger>
                <TooltipContent side="right" sideOffset={8}>
                  <div>
                    <p className="font-medium">{user?.email}</p>
                    <p className="text-xs text-muted-foreground">Free plan</p>
                  </div>
                </TooltipContent>
              </Tooltip>
            ) : (
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
                  {user?.email.charAt(0).toUpperCase()}
                </div>
                <div className="flex-1 overflow-hidden">
                  <p className="truncate text-sm font-medium">{user?.email}</p>
                  <p className="text-xs text-muted-foreground">Free plan</p>
                </div>
              </div>
            )}
          </div>
        </aside>

        {/* Main content */}
        <div className="flex flex-1 flex-col">
          {/* Header */}
          <header className="sticky top-0 z-30 flex h-16 items-center gap-4 border-b bg-card px-4 lg:px-6">
            {/* Mobile menu button */}
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0 lg:hidden"
              onClick={() => setMobileMenuOpen(true)}
            >
              <Menu className="h-5 w-5" />
            </Button>

            {/* Search */}
            <div className="flex-1 max-w-md">
              <StockSearch
                placeholder="Search stocks (e.g., AAPL, TSLA)..."
                className="w-full"
              />
            </div>

            {/* Header actions */}
            <div className="flex items-center gap-2">
              {/* Theme switcher */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <ThemeIcon className="h-5 w-5" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuLabel>Theme</DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  {themeOptions.map((option) => (
                    <DropdownMenuItem
                      key={option.value}
                      onClick={() => setTheme(option.value)}
                      className={cn(theme === option.value && 'bg-accent')}
                    >
                      <option.icon className="mr-2 h-4 w-4" />
                      {option.label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>

              <Separator orientation="vertical" className="h-6" />

              {/* User menu */}
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon">
                    <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground text-sm">
                      {user?.email.charAt(0).toUpperCase()}
                    </div>
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-56">
                  <DropdownMenuLabel>
                    <div>
                      <p className="font-medium">{user?.email}</p>
                      <p className="text-xs text-muted-foreground">Free plan</p>
                    </div>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={() => navigate('/settings')}>
                    <Settings className="mr-2 h-4 w-4" />
                    Settings
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleLogout} className="text-destructive">
                    <LogOut className="mr-2 h-4 w-4" />
                    Log out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </header>

          {/* Breadcrumb */}
          {breadcrumbs.length > 0 && (
            <div className="border-b bg-muted/30 px-4 py-2 lg:px-6">
              <nav className="flex items-center gap-1 text-sm">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-muted-foreground hover:text-foreground"
                  onClick={() => navigate('/')}
                >
                  <Home className="h-3.5 w-3.5" />
                </Button>
                {breadcrumbs.map((crumb, index) => (
                  <div key={crumb.href} className="flex items-center gap-1">
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                    {index === breadcrumbs.length - 1 ? (
                      <span className="font-medium">{crumb.label}</span>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-2 text-muted-foreground hover:text-foreground"
                        onClick={() => navigate(crumb.href)}
                      >
                        {crumb.label}
                      </Button>
                    )}
                  </div>
                ))}
              </nav>
            </div>
          )}

          {/* Page content */}
          <main className="flex-1 overflow-y-auto p-4 lg:p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </TooltipProvider>
  )
}
