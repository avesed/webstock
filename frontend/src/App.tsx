import { Routes, Route, Navigate } from 'react-router-dom'
import { lazy, useEffect } from 'react'
import { useThemeStore } from '@/stores/themeStore'
import { useAuthStore } from '@/stores/authStore'
import { Toaster } from '@/components/ui/toaster'
import { PWAUpdatePrompt } from '@/components/layout/PWAUpdatePrompt'

// Layout
import MainLayout from '@/components/layout/MainLayout'
import { AdminRoute } from '@/components/layout/AdminRoute'

// Static imports for auth pages (must load immediately)
import LoginPage from '@/pages/LoginPage'
import RegisterPage from '@/pages/RegisterPage'
import PendingApprovalPage from '@/pages/PendingApprovalPage'
import NotFoundPage from '@/pages/NotFoundPage'

// Lazy-loaded pages
const DashboardPage = lazy(() => import('@/pages/DashboardPage'))
const StockDetailPage = lazy(() => import('@/pages/StockDetailPage'))
const StockChartPage = lazy(() => import('@/pages/StockChartPage'))
const WatchlistPage = lazy(() => import('@/pages/WatchlistPage'))
const PortfolioPage = lazy(() => import('@/pages/PortfolioPage'))
const AlertsPage = lazy(() => import('@/pages/AlertsPage'))
const ReportsPage = lazy(() => import('@/pages/ReportsPage'))
const NewsPage = lazy(() => import('@/pages/NewsPage'))
const NewsReaderPage = lazy(() => import('@/pages/NewsReaderPage'))
const ChatPage = lazy(() => import('@/pages/ChatPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const AdminLayout = lazy(() => import('@/pages/admin/AdminLayout'))
const AdminUsersPage = lazy(() => import('@/pages/admin/UserManagementPage'))
const SettProvidersPage = lazy(() => import('@/pages/admin/settings/ProvidersPage'))
const SettModelsPage = lazy(() => import('@/pages/admin/settings/ModelsPage'))
const SettNewsPage = lazy(() => import('@/pages/admin/settings/NewsPage'))
const SettIntegrationsPage = lazy(() => import('@/pages/admin/settings/IntegrationsPage'))
const SettFeaturesPage = lazy(() => import('@/pages/admin/settings/FeaturesPage'))
const MonitorOverviewPage = lazy(() => import('@/pages/admin/monitor/OverviewPage'))
const MonitorFilterPage = lazy(() => import('@/pages/admin/monitor/FilterPage'))
const MonitorPipelinePage = lazy(() => import('@/pages/admin/monitor/PipelinePage'))
const AdminRssPage = lazy(() => import('@/pages/admin/RssFeedsPage'))
const AdminCostsPage = lazy(() => import('@/pages/admin/CostTrackingPage'))
const AdminKnowledgePage = lazy(() => import('@/pages/admin/KnowledgeBasePage'))
const PredStatusPage = lazy(() => import('@/pages/admin/predictions/StatusPage'))
const PredResultsPage = lazy(() => import('@/pages/admin/predictions/ResultsPage'))
const PredTriggerPage = lazy(() => import('@/pages/admin/predictions/TriggerPage'))
const PredPerformancePage = lazy(() => import('@/pages/admin/predictions/PerformancePage'))
const PredModelsPage = lazy(() => import('@/pages/admin/predictions/ModelsPage'))
const PredBacktestPage = lazy(() => import('@/pages/admin/predictions/BacktestPage'))
const PredRDAgentPage = lazy(() => import('@/pages/admin/predictions/RDAgentPage'))
const PredictionsPage = lazy(() => import('@/pages/PredictionsPage'))
const QlibBacktestsPage = lazy(() => import('@/pages/QlibBacktestsPage'))
const DiscussionPage = lazy(() => import('@/pages/DiscussionPage'))

// Protected Route wrapper
function ProtectedRoute({ children }: { readonly children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

// Public Route wrapper (redirects to dashboard if authenticated)
function PublicRoute({ children }: { readonly children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuthStore()

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    )
  }

  if (isAuthenticated) {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

function App() {
  const { theme, initTheme } = useThemeStore()
  const { initAuth } = useAuthStore()

  useEffect(() => {
    initTheme()
  }, [initTheme])

  useEffect(() => {
    initAuth()
  }, [initAuth])

  useEffect(() => {
    const root = window.document.documentElement
    root.classList.remove('light', 'dark')

    if (theme === 'system') {
      const systemTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
        ? 'dark'
        : 'light'
      root.classList.add(systemTheme)
    } else {
      root.classList.add(theme)
    }

    // Sync theme-color meta tag for PWA
    const resolvedTheme = theme === 'system'
      ? (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : theme
    const metaThemeColor = document.querySelector('meta[name="theme-color"]:not([media])')
      ?? document.querySelector('meta[name="theme-color"]')
    if (metaThemeColor) {
      metaThemeColor.setAttribute('content', resolvedTheme === 'dark' ? '#0f172a' : '#ffffff')
    }
  }, [theme])

  return (
    <>
      <Routes>
        {/* Public routes */}
        <Route
          path="/login"
          element={
            <PublicRoute>
              <LoginPage />
            </PublicRoute>
          }
        />
        <Route
          path="/register"
          element={
            <PublicRoute>
              <RegisterPage />
            </PublicRoute>
          }
        />
        <Route
          path="/pending-approval"
          element={
            <PublicRoute>
              <PendingApprovalPage />
            </PublicRoute>
          }
        />

        {/* Protected routes */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <MainLayout />
            </ProtectedRoute>
          }
        >
          <Route index element={<DashboardPage />} />
          <Route path="stock/:symbol" element={<StockDetailPage />} />
          <Route path="stock/:symbol/chart" element={<StockChartPage />} />
          <Route path="watchlist" element={<WatchlistPage />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="news" element={<NewsPage />} />
          <Route path="news/:newsId" element={<NewsReaderPage />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="discussion/:symbol" element={<DiscussionPage />} />
          <Route path="predictions" element={<PredictionsPage />} />
          <Route path="backtests" element={<Navigate to="/predictions" replace />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        {/* Admin routes — own layout with sidebar */}
        <Route
          path="/admin"
          element={
            <ProtectedRoute>
              <AdminRoute>
                <AdminLayout />
              </AdminRoute>
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<AdminUsersPage />} />
          <Route path="settings" element={<Navigate to="providers" replace />} />
          <Route path="settings/providers" element={<SettProvidersPage />} />
          <Route path="settings/models" element={<SettModelsPage />} />
          <Route path="settings/news" element={<SettNewsPage />} />
          <Route path="settings/integrations" element={<SettIntegrationsPage />} />
          <Route path="features" element={<SettFeaturesPage />} />
          <Route path="monitor" element={<Navigate to="overview" replace />} />
          <Route path="monitor/overview" element={<MonitorOverviewPage />} />
          <Route path="monitor/filter" element={<MonitorFilterPage />} />
          <Route path="monitor/pipeline" element={<MonitorPipelinePage />} />
          <Route path="filter" element={<Navigate to="/admin/monitor/filter" replace />} />
          <Route path="pipeline" element={<Navigate to="/admin/monitor/pipeline" replace />} />
          <Route path="rss" element={<AdminRssPage />} />
          <Route path="costs" element={<AdminCostsPage />} />
          <Route path="knowledge" element={<AdminKnowledgePage />} />
          <Route path="predictions" element={<Navigate to="status" replace />} />
          <Route path="predictions/status" element={<PredStatusPage />} />
          <Route path="predictions/results" element={<PredResultsPage />} />
          <Route path="predictions/trigger" element={<PredTriggerPage />} />
          <Route path="predictions/performance" element={<PredPerformancePage />} />
          <Route path="predictions/models" element={<PredModelsPage />} />
          <Route path="predictions/backtest" element={<PredBacktestPage />} />
          <Route path="predictions/rdagent" element={<PredRDAgentPage />} />
          <Route path="backtests" element={<QlibBacktestsPage />} />
        </Route>

        {/* 404 */}
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      <Toaster />
      <PWAUpdatePrompt />
    </>
  )
}

export default App
