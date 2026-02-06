import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Users,
  Activity,
  Clock,
  MessageSquare,
  FileText,
  Database,
  Cpu,
  HardDrive,
  Loader2,
  RefreshCw,
  TrendingUp,
  TrendingDown,
} from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { adminApi } from '@/api/admin'

interface StatCardProps {
  title: string
  value: string | number
  description?: string
  icon: React.ElementType
  trend?: {
    value: number
    isPositive: boolean
  }
  className?: string
}

function StatCard({ title, value, description, icon: Icon, trend, className }: StatCardProps) {
  return (
    <Card className={className}>
      <CardContent className="p-6">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm font-medium text-muted-foreground">{title}</p>
            <div className="flex items-baseline gap-2">
              <p className="text-2xl font-bold">{value}</p>
              {trend && (
                <span
                  className={cn(
                    'flex items-center text-xs font-medium',
                    trend.isPositive ? 'text-green-600' : 'text-red-600'
                  )}
                >
                  {trend.isPositive ? (
                    <TrendingUp className="mr-1 h-3 w-3" />
                  ) : (
                    <TrendingDown className="mr-1 h-3 w-3" />
                  )}
                  {trend.value}%
                </span>
              )}
            </div>
            {description && <p className="text-xs text-muted-foreground">{description}</p>}
          </div>
          <div className="rounded-full bg-primary/10 p-3">
            <Icon className="h-5 w-5 text-primary" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

interface ProgressBarProps {
  value: number
  max?: number
  label: string
  color?: 'default' | 'warning' | 'danger'
}

function ProgressBar({ value, max = 100, label, color = 'default' }: ProgressBarProps) {
  const percentage = Math.min((value / max) * 100, 100)

  const colorClasses = {
    default: 'bg-primary',
    warning: 'bg-yellow-500',
    danger: 'bg-red-500',
  }

  const getColor = () => {
    if (percentage > 90) return colorClasses.danger
    if (percentage > 75) return colorClasses.warning
    return colorClasses[color]
  }

  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium">{value.toFixed(1)}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-muted">
        <div
          className={cn('h-full rounded-full transition-all', getColor())}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </div>
  )
}

function formatUptime(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)

  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  return `${minutes}m`
}

function formatNumber(num: number): string {
  if (num >= 1000000) {
    return (num / 1000000).toFixed(1) + 'M'
  }
  if (num >= 1000) {
    return (num / 1000).toFixed(1) + 'K'
  }
  return num.toString()
}

export function SystemMonitor() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')

  // Query with 30-second auto-refresh
  const { data: stats, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ['admin-system-stats'],
    queryFn: adminApi.getSystemStats,
    refetchInterval: 30000, // 30 seconds
  })

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64">
          <p className="text-destructive">{tCommon('status.error')}</p>
        </CardContent>
      </Card>
    )
  }

  if (!stats) return null

  return (
    <div className="space-y-6">
      {/* Refresh Button */}
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw className={cn('mr-2 h-4 w-4', isFetching && 'animate-spin')} />
          {tCommon('actions.refresh')}
        </Button>
      </div>

      {/* User Stats */}
      <div>
        <h3 className="text-lg font-medium mb-4">{t('monitor.userStats')}</h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title={t('monitor.totalUsers')}
            value={formatNumber(stats.users.total)}
            icon={Users}
          />
          <StatCard
            title={t('monitor.activeUsers')}
            value={formatNumber(stats.users.active)}
            description={t('monitor.last30Days')}
            icon={Activity}
          />
          <StatCard
            title={t('monitor.newToday')}
            value={stats.users.newToday}
            icon={Users}
            trend={{ value: 12, isPositive: true }}
          />
          <StatCard
            title={t('monitor.newThisWeek')}
            value={stats.users.newThisWeek}
            icon={Users}
          />
        </div>
      </div>

      {/* Activity Stats */}
      <div>
        <h3 className="text-lg font-medium mb-4">{t('monitor.activityStats')}</h3>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            title={t('monitor.todayLogins')}
            value={stats.activity.todayLogins}
            icon={Clock}
          />
          <StatCard
            title={t('monitor.activeConversations')}
            value={stats.activity.activeConversations}
            icon={MessageSquare}
          />
          <StatCard
            title={t('monitor.reportsGenerated')}
            value={stats.activity.reportsGenerated}
            description={t('monitor.today')}
            icon={FileText}
          />
          <StatCard
            title={t('monitor.apiCalls')}
            value={formatNumber(stats.activity.apiCallsToday)}
            description={t('monitor.today')}
            icon={Activity}
          />
        </div>
      </div>

      {/* System Resources */}
      <Card>
        <CardHeader>
          <CardTitle>{t('monitor.systemResources')}</CardTitle>
          <CardDescription>{t('monitor.systemResourcesDescription')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex items-center gap-4">
              <div className="rounded-full bg-primary/10 p-3">
                <Clock className="h-5 w-5 text-primary" />
              </div>
              <div>
                <p className="text-sm text-muted-foreground">{t('monitor.uptime')}</p>
                <p className="text-lg font-bold">{formatUptime(stats.system.uptime)}</p>
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="rounded-full bg-primary/10 p-3">
                <Cpu className="h-5 w-5 text-primary" />
              </div>
              <div className="flex-1">
                <ProgressBar value={stats.system.cpuUsage} label={t('monitor.cpu')} />
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="rounded-full bg-primary/10 p-3">
                <Database className="h-5 w-5 text-primary" />
              </div>
              <div className="flex-1">
                <ProgressBar value={stats.system.memoryUsage} label={t('monitor.memory')} />
              </div>
            </div>

            <div className="flex items-center gap-4">
              <div className="rounded-full bg-primary/10 p-3">
                <HardDrive className="h-5 w-5 text-primary" />
              </div>
              <div className="flex-1">
                <ProgressBar value={stats.system.diskUsage} label={t('monitor.disk')} />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* API Performance */}
      <Card>
        <CardHeader>
          <CardTitle>{t('monitor.apiPerformance')}</CardTitle>
          <CardDescription>{t('monitor.apiPerformanceDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t('monitor.totalRequests')}</p>
              <p className="text-2xl font-bold">{formatNumber(stats.api.totalRequests)}</p>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t('monitor.avgLatency')}</p>
              <p className="text-2xl font-bold">{stats.api.averageLatency.toFixed(0)}ms</p>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t('monitor.errorRate')}</p>
              <p
                className={cn(
                  'text-2xl font-bold',
                  stats.api.errorRate > 5 ? 'text-red-600' : stats.api.errorRate > 1 ? 'text-yellow-600' : 'text-green-600'
                )}
              >
                {stats.api.errorRate.toFixed(2)}%
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-sm text-muted-foreground">{t('monitor.rateLimitHits')}</p>
              <p className="text-2xl font-bold">{stats.api.rateLimitHits}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Auto-refresh indicator */}
      <p className="text-center text-xs text-muted-foreground">
        {t('monitor.autoRefresh')}
      </p>
    </div>
  )
}
