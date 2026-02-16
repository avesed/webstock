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
const AdminDashboardPage = lazy(() => import('@/pages/admin/AdminDashboardPage'))
const QlibBacktestsPage = lazy(() => import('@/pages/QlibBacktestsPage'))

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
          <Route path="backtests" element={<QlibBacktestsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route
            path="admin"
            element={
              <AdminRoute>
                <AdminDashboardPage />
              </AdminRoute>
            }
          />
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
