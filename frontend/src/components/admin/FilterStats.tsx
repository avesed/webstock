import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, BarChart3, CheckCircle, Filter, TrendingUp, TrendingDown, Play, Loader2, Clock, Zap, Download, ImageIcon, Sparkles, Calendar, X } from 'lucide-react'

import { adminApi, FilterStats as FilterStatsType, MonitorStatus, SourceStats, NewsPipelineStats, Layer15Stats } from '@/api/admin'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

interface StatCardProps {
  title: string
  value: string | number
  subtitle?: string
  icon?: React.ReactNode
  trend?: 'up' | 'down' | 'neutral'
  className?: string
}

function StatCard({ title, value, subtitle, icon, trend, className }: StatCardProps) {
  return (
    <Card className={cn('', className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {subtitle && (
          <p className="text-xs text-muted-foreground flex items-center gap-1">
            {trend === 'up' && <TrendingUp className="h-3 w-3 text-green-500" />}
            {trend === 'down' && <TrendingDown className="h-3 w-3 text-red-500" />}
            {subtitle}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function useCountdown(targetIso: string | null | undefined) {
  const [remaining, setRemaining] = useState('')

  useEffect(() => {
    if (!targetIso) {
      setRemaining('')
      return
    }

    const update = () => {
      const diff = new Date(targetIso).getTime() - Date.now()
      if (diff <= 0) {
        setRemaining('any moment')
        return
      }
      const mins = Math.floor(diff / 60000)
      const secs = Math.floor((diff % 60000) / 1000)
      setRemaining(`${mins}:${secs.toString().padStart(2, '0')}`)
    }

    update()
    const id = setInterval(update, 1000)
    return () => clearInterval(id)
  }, [targetIso])

  return remaining
}

export default function FilterStats() {
  const { t } = useTranslation('admin')
  const [isTriggering, setIsTriggering] = useState(false)
  const [triggerMessage, setTriggerMessage] = useState<string | null>(null)
  const [statsDays, setStatsDays] = useState(7)
  const [errorsDismissed, setErrorsDismissed] = useState(false)

  const periodOptions = [
    { days: 1, label: t('filter.periodToday') },
    { days: 7, label: t('filter.periodWeek') },
    { days: 30, label: t('filter.periodMonth') },
  ] as const

  const { data: stats, isLoading, error, refetch } = useQuery<FilterStatsType>({
    queryKey: ['admin', 'filter-stats', statsDays],
    queryFn: () => adminApi.getFilterStats(statsDays),
    refetchInterval: 60000,
  })

  const { data: sourceStats, isLoading: sourceLoading } = useQuery<SourceStats>({
    queryKey: ['admin', 'source-stats', statsDays],
    queryFn: () => adminApi.getSourceStats(statsDays),
    refetchInterval: 60000,
  })

  const { data: newsPipelineStats } = useQuery<NewsPipelineStats>({
    queryKey: ['admin', 'news-pipeline-stats', statsDays],
    queryFn: () => adminApi.getNewsPipelineStats(statsDays),
    refetchInterval: 60000,
  })

  const { data: layer15Stats } = useQuery<Layer15Stats>({
    queryKey: ['admin', 'layer15-stats', statsDays],
    queryFn: () => adminApi.getLayer15Stats(statsDays),
    refetchInterval: 60000,
  })

  // Poll monitor status (fast when running, slow when idle)
  const { data: monitorStatus } = useQuery<MonitorStatus>({
    queryKey: ['admin', 'monitor-status'],
    queryFn: () => adminApi.getMonitorStatus(),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'running' ? 2000 : 15000
    },
  })

  const isRunning = monitorStatus?.status === 'running'
  const countdown = useCountdown(monitorStatus?.nextRunAt)

  // Auto-refresh stats when task finishes
  useEffect(() => {
    if (monitorStatus?.status === 'idle' && isTriggering) {
      setIsTriggering(false)
      refetch()
    }
  }, [monitorStatus?.status, isTriggering, refetch])

  const handleTriggerMonitor = async () => {
    setIsTriggering(true)
    setTriggerMessage(null)
    try {
      await adminApi.triggerNewsMonitor()
      setTriggerMessage(t('filter.taskTriggered'))
    } catch {
      setTriggerMessage('Failed to trigger news monitor')
      setIsTriggering(false)
    }
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i}>
              <CardHeader className="pb-2">
                <Skeleton className="h-4 w-24" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    )
  }

  if (error || !stats) {
    return (
      <Card>
        <CardContent className="pt-6">
          <p className="text-muted-foreground text-center">
            {t('filter.noData')}
          </p>
          <p className="text-xs text-muted-foreground text-center mt-2">
            {t('filter.noDataHint')}
          </p>
        </CardContent>
      </Card>
    )
  }

  const initialFilter = stats.counts?.initialFilter ?? { useful: 0, uncertain: 0, skip: 0, total: 0 }
  const deepFilter = stats.counts?.deepFilter ?? { keep: 0, delete: 0, total: 0 }
  const errors = stats.counts?.errors ?? { filterError: 0, embeddingError: 0 }
  const embedding = stats.counts?.embedding ?? { success: 0, error: 0 }

  const rates = stats.rates ?? {
    initialPassRate: 0,
    initialSkipRate: 0,
    deepKeepRate: 0,
    deepDeleteRate: 0,
    filterErrorRate: 0,
    embeddingErrorRate: 0,
  }

  // Layer 1 three-agent scoring
  const layer1Scoring = stats.counts?.layer1Scoring ?? null
  const hasLayer1 = (layer1Scoring?.total ?? 0) > 0

  const alerts = stats.alerts ?? []

  return (
    <div className="space-y-6">
      {/* Monitor Control Panel */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Button
                onClick={handleTriggerMonitor}
                disabled={isTriggering || isRunning}
                size="sm"
              >
                {isRunning || isTriggering ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Play className="h-4 w-4 mr-2" />
                )}
                {isRunning ? t('filter.running') : t('filter.triggerMonitor')}
              </Button>
              {triggerMessage && !isRunning && (
                <span className="text-sm text-muted-foreground">{triggerMessage}</span>
              )}
            </div>
            <div className="flex items-center gap-3">
              {/* Time Period Selector */}
              <div className="flex items-center gap-1 rounded-lg border bg-muted/40 p-0.5">
                <Calendar className="h-3.5 w-3.5 ml-2 text-muted-foreground" />
                {periodOptions.map(opt => (
                  <button
                    key={opt.days}
                    onClick={() => setStatsDays(opt.days)}
                    className={cn(
                      'px-2.5 py-1 text-xs font-medium rounded-md transition-colors',
                      statsDays === opt.days
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {!isRunning && countdown && (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Clock className="h-4 w-4" />
                  <span>{t('filter.nextRun')}: {countdown}</span>
                </div>
              )}
            </div>
          </div>

          {/* Progress bar when running */}
          {isRunning && monitorStatus?.progress && (
            <div className="mt-4 space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{monitorStatus.progress.message}</span>
                <span className="font-medium">{monitorStatus.progress.percent}%</span>
              </div>
              <Progress value={monitorStatus.progress.percent} className="h-2" />
            </div>
          )}

          {/* Last run summary */}
          {!isRunning && monitorStatus?.lastRun && (
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{t('filter.lastRun')}: {new Date(monitorStatus.lastRun.finishedAt).toLocaleTimeString()}</span>
              {monitorStatus.lastRun.stats && (
                <>
                  <span>Global: {monitorStatus.lastRun.stats.global_fetched ?? 0}</span>
                  <span>Watchlist: {monitorStatus.lastRun.stats.watchlist_fetched ?? 0}</span>
                  <span>A-Share: {monitorStatus.lastRun.stats.ashare_fetched ?? 0}</span>
                  <span>Stored: {monitorStatus.lastRun.stats.articles_stored ?? 0}</span>
                </>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Error Banner (dismissible) */}
      {!errorsDismissed && (errors.filterError > 0 || errors.embeddingError > 0) && (
        <div className="flex items-center gap-3 p-3 rounded-lg border border-red-200 dark:border-red-800 bg-red-50 dark:bg-red-950">
          <AlertTriangle className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0" />
          <div className="flex-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="font-medium text-red-700 dark:text-red-300">{t('filter.errors')}</span>
            {errors.filterError > 0 && (
              <span className="text-red-600 dark:text-red-400">
                {t('filter.filterError')}: {errors.filterError} ({rates.filterErrorRate}%)
              </span>
            )}
            {errors.embeddingError > 0 && (
              <span className="text-red-600 dark:text-red-400">
                {t('filter.embeddingError')}: {errors.embeddingError} ({rates.embeddingErrorRate}%)
              </span>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-900"
            onClick={() => setErrorsDismissed(true)}
          >
            <X className="h-3.5 w-3.5" />
            <span className="ml-1 text-xs">{t('filter.dismissErrors')}</span>
          </Button>
        </div>
      )}

      {/* Alerts */}
      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((alert, i) => (
            <div
              key={i}
              className={cn(
                'flex items-center gap-2 p-3 rounded-lg border',
                alert.level === 'critical'
                  ? 'bg-red-50 border-red-200 text-red-800 dark:bg-red-950 dark:border-red-800 dark:text-red-200'
                  : 'bg-yellow-50 border-yellow-200 text-yellow-800 dark:bg-yellow-950 dark:border-yellow-800 dark:text-yellow-200'
              )}
            >
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span className="text-sm">{alert.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* Summary Cards */}
      <div className="grid gap-4 md:grid-cols-2">
        <StatCard
          title={hasLayer1 ? t('filter.layer1Scoring') : t('filter.initialFilter')}
          value={hasLayer1 ? layer1Scoring!.total : initialFilter.total}
          subtitle={`${hasLayer1 ? (rates.layer1PassRate ?? 0) : rates.initialPassRate}% ${t('filter.passRate')}`}
          icon={<Filter className="h-4 w-4 text-muted-foreground" />}
        />
        <StatCard
          title={hasLayer1 ? t('filter.layer3Pipeline') : t('filter.deepFilter')}
          value={hasLayer1 ? (newsPipelineStats?.routing.total ?? 0) : deepFilter.total}
          subtitle={hasLayer1
            ? `${newsPipelineStats?.routing.fullAnalysis ?? 0} ${t('filter.pathFull')} / ${newsPipelineStats?.routing.lightweight ?? 0} ${t('filter.pathLite')}`
            : `${rates.deepKeepRate}% ${t('filter.passRate')}`}
          icon={<CheckCircle className="h-4 w-4 text-muted-foreground" />}
        />
      </div>

      {/* Detailed Stats */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Left card: Layer 1 Scoring (new) or Initial Filter (legacy) */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Filter className="h-4 w-4" />
              {hasLayer1 ? t('filter.layer1Scoring') : t('filter.initialFilter')}
            </CardTitle>
            <CardDescription>
              {hasLayer1 ? t('filter.layer1Description') : t('filter.description')}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {hasLayer1 && layer1Scoring ? (
              <>
                {/* Layer 1 scoring progress bars */}
                {[
                  { label: t('filter.layer1FullAnalysis'), count: layer1Scoring.fullAnalysis, color: 'bg-green-500', badgeVariant: 'default' as const, badgeClass: 'bg-green-500' },
                  { label: t('filter.layer1Lightweight'), count: layer1Scoring.lightweight, color: 'bg-yellow-500', badgeVariant: 'secondary' as const, badgeClass: 'bg-yellow-500 text-white' },
                  { label: t('filter.layer1Discard'), count: layer1Scoring.discard, color: 'bg-slate-400', badgeVariant: 'outline' as const, badgeClass: '' },
                ].map(({ label, count, badgeVariant, badgeClass }) => (
                  <div key={label} className="space-y-2">
                    <div className="flex justify-between text-sm">
                      <span className="flex items-center gap-2">
                        <Badge variant={badgeVariant} className={badgeClass}>{label}</Badge>
                        {count}
                      </span>
                      <span className="text-muted-foreground">
                        {layer1Scoring.total > 0
                          ? ((count / layer1Scoring.total) * 100).toFixed(1)
                          : 0}%
                      </span>
                    </div>
                    <Progress
                      value={layer1Scoring.total > 0 ? (count / layer1Scoring.total) * 100 : 0}
                      className="h-2"
                    />
                  </div>
                ))}

                {/* Critical event badge (only when > 0) */}
                {layer1Scoring.criticalEvent > 0 && (
                  <div className="flex items-center gap-2">
                    <Badge variant="destructive" className="text-xs">
                      <Zap className="h-3 w-3 mr-1" />
                      {t('filter.layer1CriticalEvent')}: {layer1Scoring.criticalEvent}
                    </Badge>
                  </div>
                )}

              </>
            ) : (
              <>
                {/* Legacy: Initial Filter breakdown */}
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="flex items-center gap-2">
                      <Badge variant="default" className="bg-green-500">{t('filter.useful')}</Badge>
                      {initialFilter.useful}
                    </span>
                    <span className="text-muted-foreground">
                      {initialFilter.total > 0
                        ? ((initialFilter.useful / initialFilter.total) * 100).toFixed(1)
                        : 0}%
                    </span>
                  </div>
                  <Progress
                    value={initialFilter.total > 0 ? (initialFilter.useful / initialFilter.total) * 100 : 0}
                    className="h-2"
                  />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="flex items-center gap-2">
                      <Badge variant="secondary">{t('filter.uncertain')}</Badge>
                      {initialFilter.uncertain}
                    </span>
                    <span className="text-muted-foreground">
                      {initialFilter.total > 0
                        ? ((initialFilter.uncertain / initialFilter.total) * 100).toFixed(1)
                        : 0}%
                    </span>
                  </div>
                  <Progress
                    value={initialFilter.total > 0 ? (initialFilter.uncertain / initialFilter.total) * 100 : 0}
                    className="h-2"
                  />
                </div>
                <div className="space-y-2">
                  <div className="flex justify-between text-sm">
                    <span className="flex items-center gap-2">
                      <Badge variant="outline">{t('filter.skip')}</Badge>
                      {initialFilter.skip}
                    </span>
                    <span className="text-muted-foreground">
                      {initialFilter.total > 0
                        ? ((initialFilter.skip / initialFilter.total) * 100).toFixed(1)
                        : 0}%
                    </span>
                  </div>
                  <Progress
                    value={initialFilter.total > 0 ? (initialFilter.skip / initialFilter.total) * 100 : 0}
                    className="h-2"
                  />
                </div>

              </>
            )}
          </CardContent>
        </Card>

        {hasLayer1 && newsPipelineStats ? (
          /* Score Distribution — moved here from Layer 3 since scoring is Layer 1 */
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <BarChart3 className="h-4 w-4" />
                {t('filter.scoreDistribution')}
              </CardTitle>
              <CardDescription>
                {t('filter.scoreDistDescription')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              {newsPipelineStats.scoreDistribution.length > 0 ? (
                (() => {
                  const maxCount = Math.max(...newsPipelineStats.scoreDistribution.map(b => b.count), 1)
                  const bucketColors: Record<string, string> = {
                    '0-59': 'bg-red-500',
                    '60-104': 'bg-orange-500',
                    '105-149': 'bg-yellow-500',
                    '150-194': 'bg-lime-500',
                    '195-300': 'bg-green-500',
                  }
                  return newsPipelineStats.scoreDistribution.map((bucket) => (
                    <div key={bucket.bucket} className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground w-16 text-right font-mono">{bucket.bucket}</span>
                      <div className="flex-1 relative h-5 rounded bg-muted overflow-hidden">
                        <div
                          className={cn('absolute inset-y-0 left-0 rounded transition-all', bucketColors[bucket.bucket] ?? 'bg-blue-500')}
                          style={{ width: `${(bucket.count / maxCount) * 100}%` }}
                        />
                        {bucket.count > 0 && (
                          <span className="absolute inset-y-0 flex items-center text-xs font-medium ml-2 text-white drop-shadow-sm">
                            {bucket.count}
                          </span>
                        )}
                      </div>
                      <div className="w-32 text-xs text-muted-foreground flex items-center justify-end gap-2">
                        {bucket.fullAnalysis > 0 && (
                          <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                            <span className="h-2 w-2 rounded-full bg-blue-500 inline-block shrink-0" />
                            {t('filter.pathFull')} {bucket.fullAnalysis}
                          </span>
                        )}
                        {bucket.lightweight > 0 && (
                          <span className="flex items-center gap-1 text-slate-600 dark:text-slate-400">
                            <span className="h-2 w-2 rounded-full bg-slate-400 inline-block shrink-0" />
                            {t('filter.pathLite')} {bucket.lightweight}
                          </span>
                        )}
                      </div>
                    </div>
                  ))
                })()
              ) : (
                <p className="text-sm text-muted-foreground text-center py-2">-</p>
              )}

              {/* Embedding stats inline */}
              <div className="pt-4 border-t">
                <p className="text-sm text-muted-foreground mb-2">Embedding</p>
                <div className="flex items-center gap-4 text-sm">
                  <span className="flex items-center gap-1">
                    <CheckCircle className="h-3 w-3 text-green-500" />
                    {embedding.success}
                  </span>
                  {embedding.error > 0 && (
                    <span className="flex items-center gap-1 text-red-500">
                      <AlertTriangle className="h-3 w-3" />
                      {embedding.error}
                    </span>
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          /* Legacy: Deep Filter Breakdown */
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t('filter.deepFilter')}</CardTitle>
              <CardDescription>{t('filter.description')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <Badge variant="default" className="bg-green-500">{t('filter.keep')}</Badge>
                    {deepFilter.keep}
                  </span>
                  <span className="text-muted-foreground">
                    {deepFilter.total > 0
                      ? ((deepFilter.keep / deepFilter.total) * 100).toFixed(1)
                      : 0}%
                  </span>
                </div>
                <Progress
                  value={deepFilter.total > 0 ? (deepFilter.keep / deepFilter.total) * 100 : 0}
                  className="h-2"
                />
              </div>
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="flex items-center gap-2">
                    <Badge variant="destructive">{t('filter.delete')}</Badge>
                    {deepFilter.delete}
                  </span>
                  <span className="text-muted-foreground">
                    {deepFilter.total > 0
                      ? ((deepFilter.delete / deepFilter.total) * 100).toFixed(1)
                      : 0}%
                  </span>
                </div>
                <Progress
                  value={deepFilter.total > 0 ? (deepFilter.delete / deepFilter.total) * 100 : 0}
                  className="h-2"
                />
              </div>

              {/* Embedding stats */}
              <div className="pt-4 border-t">
                <p className="text-sm font-medium mb-2">Embedding</p>
                <div className="flex items-center gap-4 text-sm">
                  <span className="flex items-center gap-1">
                    <CheckCircle className="h-3 w-3 text-green-500" />
                    {embedding.success}
                  </span>
                  {embedding.error > 0 && (
                    <span className="flex items-center gap-1 text-red-500">
                      <AlertTriangle className="h-3 w-3" />
                      {embedding.error}
                    </span>
                  )}
                </div>
              </div>

            </CardContent>
          </Card>
        )}
      </div>

      {/* Error Stats — moved to top as dismissible banner */}

      {/* Layer 2 Content Fetch & Cleaning Section */}
      {layer15Stats && layer15Stats.fetch.total > 0 && (
        <>
          <Separator />
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold">
              {hasLayer1 ? t('filter.layer2Title') : t('filter.layer15Title')}
            </h3>
            <Badge variant="secondary">Layer 2</Badge>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            {/* Card 1: Fetch Overview (left) */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Download className="h-4 w-4" />
                  {t('filter.fetchOverview')}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {/* Success/Error stacked bar */}
                <div className="relative h-5 rounded-full overflow-hidden bg-muted">
                  {layer15Stats.fetch.total > 0 && (
                    <>
                      <div
                        className="absolute inset-y-0 left-0 bg-green-500 transition-all"
                        style={{ width: `${(layer15Stats.fetch.success / layer15Stats.fetch.total) * 100}%` }}
                      />
                      <div
                        className="absolute inset-y-0 bg-red-400 transition-all"
                        style={{
                          left: `${(layer15Stats.fetch.success / layer15Stats.fetch.total) * 100}%`,
                          width: `${(layer15Stats.fetch.errors / layer15Stats.fetch.total) * 100}%`,
                        }}
                      />
                    </>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-4 text-sm">
                  <span className="flex items-center gap-1.5">
                    <span className="h-3 w-3 rounded-full bg-green-500 inline-block" />
                    {t('filter.fetchSuccess')}: <strong>{layer15Stats.fetch.success}</strong>
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="h-3 w-3 rounded-full bg-red-400 inline-block" />
                    {t('filter.fetchErrors')}: <strong>{layer15Stats.fetch.errors}</strong>
                  </span>
                  <span className="text-muted-foreground">
                    {t('filter.fetchSuccessRate')}: {layer15Stats.fetch.total > 0
                      ? `${((layer15Stats.fetch.success / layer15Stats.fetch.total) * 100).toFixed(1)}%`
                      : '-'}
                  </span>
                </div>

                {/* Latency stats */}
                <div className="grid grid-cols-3 gap-2 pt-2 border-t text-xs text-muted-foreground">
                  <div className="text-center">
                    <div className="font-mono font-medium text-foreground text-sm">
                      {layer15Stats.fetch.avgMs != null ? `${layer15Stats.fetch.avgMs.toFixed(0)}` : '-'}
                    </div>
                    <div>Avg(ms)</div>
                  </div>
                  <div className="text-center">
                    <div className="font-mono font-medium text-foreground text-sm">
                      {layer15Stats.fetch.p50Ms != null ? `${layer15Stats.fetch.p50Ms.toFixed(0)}` : '-'}
                    </div>
                    <div>P50(ms)</div>
                  </div>
                  <div className="text-center">
                    <div className="font-mono font-medium text-foreground text-sm">
                      {layer15Stats.fetch.p95Ms != null ? `${layer15Stats.fetch.p95Ms.toFixed(0)}` : '-'}
                    </div>
                    <div>P95(ms)</div>
                  </div>
                </div>

                {/* Provider distribution */}
                {layer15Stats.providerDistribution.length > 0 && (
                  <div className="pt-2 border-t">
                    <div className="text-xs text-muted-foreground mb-1">{t('filter.providerDistribution')}</div>
                    <div className="flex flex-wrap gap-1.5">
                      {layer15Stats.providerDistribution.map((p) => (
                        <Badge key={p.provider} variant="outline" className="text-xs font-mono">
                          {p.provider}: {p.count}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Card 2: Image Extraction (right) */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <ImageIcon className="h-4 w-4" />
                  {t('filter.imageExtraction')}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {/* Big number: articles with images */}
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-bold">
                    {layer15Stats.fetch.articlesWithImages}
                  </span>
                  <span className="text-sm text-muted-foreground">
                    / {layer15Stats.fetch.success} {t('filter.articlesWithImages')}
                  </span>
                </div>
                {layer15Stats.fetch.success > 0 && (
                  <Progress
                    value={(layer15Stats.fetch.articlesWithImages / layer15Stats.fetch.success) * 100}
                    className="h-2"
                  />
                )}
                <div className="grid grid-cols-2 gap-3 pt-2 border-t text-sm">
                  <div>
                    <span className="text-muted-foreground">{t('filter.avgImagesFound')}:</span>{' '}
                    <span className="font-medium">{layer15Stats.fetch.avgImagesFound.toFixed(1)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">{t('filter.avgImagesDownloaded')}:</span>{' '}
                    <span className="font-medium">{layer15Stats.fetch.avgImagesDownloaded.toFixed(1)}</span>
                  </div>
                </div>

                {/* Content cleaning summary in same card */}
                {layer15Stats.cleaning.total > 0 && (
                  <div className="pt-3 border-t space-y-2">
                    <div className="flex items-center gap-2">
                      <Sparkles className="h-3.5 w-3.5 text-muted-foreground" />
                      <span className="text-sm font-medium">{t('filter.contentCleaning')}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <span className="text-muted-foreground">{t('filter.cleaningRuns')}:</span>{' '}
                        <span className="font-medium">{layer15Stats.cleaning.success}</span>
                        {layer15Stats.cleaning.errors > 0 && (
                          <span className="text-red-500 ml-1">({layer15Stats.cleaning.errors} err)</span>
                        )}
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('filter.avgRetentionRate')}:</span>{' '}
                        <span className="font-medium">
                          {layer15Stats.cleaning.avgRetentionRate != null
                            ? `${(layer15Stats.cleaning.avgRetentionRate * 100).toFixed(1)}%`
                            : '-'}
                        </span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('filter.visualDataArticles')}:</span>{' '}
                        <span className="font-medium">{layer15Stats.cleaning.articlesWithVisualData}</span>
                      </div>
                      <div>
                        <span className="text-muted-foreground">{t('filter.cleaningLatency')}:</span>{' '}
                        <span className="font-mono text-xs font-medium">
                          {layer15Stats.cleaning.avgMs != null ? `${layer15Stats.cleaning.avgMs.toFixed(0)}ms` : '-'}
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {/* Layer 3 Analysis Pipeline Section */}
      {newsPipelineStats && newsPipelineStats.routing.total > 0 && (
        <>
          <Separator />
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold">
              {hasLayer1 ? t('filter.layer3Title') : t('filter.phase2Title')}
            </h3>
            <Badge variant="secondary">{hasLayer1 ? 'Layer 3' : 'Phase 2'}</Badge>
          </div>

          {/* Card 1: Processing Path Split (full-width) */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t('filter.processingPath')}</CardTitle>
              <CardDescription>{hasLayer1 ? t('filter.layer3Description') : t('filter.phase2Description')}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {/* Stacked bar */}
              <div className="relative h-6 rounded-full overflow-hidden bg-muted">
                {newsPipelineStats.routing.total > 0 && (
                  <>
                    <div
                      className="absolute inset-y-0 left-0 bg-blue-500 transition-all"
                      style={{ width: `${(newsPipelineStats.routing.fullAnalysis / newsPipelineStats.routing.total) * 100}%` }}
                    />
                    <div
                      className="absolute inset-y-0 bg-slate-400 transition-all"
                      style={{
                        left: `${(newsPipelineStats.routing.fullAnalysis / newsPipelineStats.routing.total) * 100}%`,
                        width: `${(newsPipelineStats.routing.lightweight / newsPipelineStats.routing.total) * 100}%`,
                      }}
                    />
                  </>
                )}
              </div>
              {/* Counts */}
              <div className="flex flex-wrap items-center gap-4 text-sm">
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 rounded-full bg-blue-500 inline-block" />
                  {t('filter.fullAnalysis')}: <strong>{newsPipelineStats.routing.fullAnalysis}</strong>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-3 w-3 rounded-full bg-slate-400 inline-block" />
                  {t('filter.lightweightProcessing')}: <strong>{newsPipelineStats.routing.lightweight}</strong>
                </span>
                {newsPipelineStats.routing.criticalEvents > 0 && (
                  <Badge variant="destructive" className="text-xs">
                    <Zap className="h-3 w-3 mr-1" />
                    {t('filter.criticalEvents')}: {newsPipelineStats.routing.criticalEvents}
                  </Badge>
                )}
                {newsPipelineStats.routing.scoringErrors > 0 && (
                  <span className="text-red-500 flex items-center gap-1">
                    <AlertTriangle className="h-3 w-3" />
                    {t('filter.scoringErrors')}: {newsPipelineStats.routing.scoringErrors}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            {/* Score Distribution — only in legacy mode (Layer 1 mode shows it under Layer 1 section) */}
            {!hasLayer1 && (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base flex items-center gap-2">
                    <BarChart3 className="h-4 w-4" />
                    {t('filter.scoreDistribution')}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  {newsPipelineStats.scoreDistribution.length > 0 ? (
                    (() => {
                      const maxCount = Math.max(...newsPipelineStats.scoreDistribution.map(b => b.count), 1)
                      const bucketColors: Record<string, string> = {
                        '0-19': 'bg-red-500',
                        '20-39': 'bg-orange-500',
                        '40-59': 'bg-yellow-500',
                        '60-79': 'bg-lime-500',
                        '80-100': 'bg-green-500',
                      }
                      return newsPipelineStats.scoreDistribution.map((bucket) => (
                        <div key={bucket.bucket} className="flex items-center gap-2">
                          <span className="text-xs text-muted-foreground w-16 text-right font-mono">{bucket.bucket}</span>
                          <div className="flex-1 relative h-5 rounded bg-muted overflow-hidden">
                            <div
                              className={cn('absolute inset-y-0 left-0 rounded transition-all', bucketColors[bucket.bucket] ?? 'bg-blue-500')}
                              style={{ width: `${(bucket.count / maxCount) * 100}%` }}
                            />
                            {bucket.count > 0 && (
                              <span className="absolute inset-y-0 flex items-center text-xs font-medium ml-2 text-white drop-shadow-sm">
                                {bucket.count}
                              </span>
                            )}
                          </div>
                          <div className="w-32 text-xs text-muted-foreground flex items-center justify-end gap-2">
                            {bucket.fullAnalysis > 0 && (
                              <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                                <span className="h-2 w-2 rounded-full bg-blue-500 inline-block shrink-0" />
                                {t('filter.pathFull')} {bucket.fullAnalysis}
                              </span>
                            )}
                            {bucket.lightweight > 0 && (
                              <span className="flex items-center gap-1 text-slate-600 dark:text-slate-400">
                                <span className="h-2 w-2 rounded-full bg-slate-400 inline-block shrink-0" />
                                {t('filter.pathLite')} {bucket.lightweight}
                              </span>
                            )}
                          </div>
                        </div>
                      ))
                    })()
                  ) : (
                    <p className="text-sm text-muted-foreground text-center py-2">-</p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Node Latency Table */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base flex items-center gap-2">
                  <Clock className="h-4 w-4" />
                  {t('filter.nodeLatency')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {newsPipelineStats.nodeLatency.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b text-muted-foreground">
                          <th className="text-left py-1.5 font-medium">{t('filter.nodeColumn')}</th>
                          <th className="text-right py-1.5 font-medium">{t('filter.countColumn')}</th>
                          <th className="text-right py-1.5 font-medium">{t('filter.successColumn')}</th>
                          <th className="text-right py-1.5 font-medium">{t('filter.errorsColumn')}</th>
                          <th className="text-right py-1.5 font-medium">{t('filter.avgMsColumn')}</th>
                          <th className="text-right py-1.5 font-medium">{t('filter.p95MsColumn')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {newsPipelineStats.nodeLatency.map((node) => (
                          <tr key={node.node} className="border-b last:border-0">
                            <td className="py-1.5 font-mono text-xs">{node.node}</td>
                            <td className="text-right py-1.5">{node.count}</td>
                            <td className="text-right py-1.5 text-green-600 dark:text-green-400">{node.success}</td>
                            <td className="text-right py-1.5">
                              {node.errors > 0 ? (
                                <span className="text-red-600 dark:text-red-400">{node.errors}</span>
                              ) : (
                                <span className="text-muted-foreground">0</span>
                              )}
                            </td>
                            <td className="text-right py-1.5 font-mono text-xs">
                              {node.avgMs != null ? node.avgMs.toFixed(0) : '-'}
                            </td>
                            <td className="text-right py-1.5 font-mono text-xs">
                              {node.p95Ms != null ? node.p95Ms.toFixed(0) : '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground text-center py-4">-</p>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      {/* Source Quality Ranking */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('filter.sourceTitle')}</CardTitle>
          <CardDescription>{t('filter.sourceDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          {sourceLoading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !sourceStats?.sources?.length ? (
            <div className="text-center py-4">
              <p className="text-muted-foreground">{t('filter.noSourceData')}</p>
              <p className="text-xs text-muted-foreground mt-1">{t('filter.noSourceDataHint')}</p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 font-medium">{t('filter.sourceColumn')}</th>
                    <th className="text-right py-2 font-medium">{t('filter.totalColumn')}</th>
                    <th className="text-right py-2 font-medium">{t('filter.keepRateColumn')}</th>
                    <th className="text-right py-2 font-medium">{t('filter.fetchRateColumn')}</th>
                    <th className="text-right py-2 font-medium">{t('filter.avgEntitiesColumn')}</th>
                    <th className="text-right py-2 font-medium">{t('filter.sentimentColumn')}</th>
                  </tr>
                </thead>
                <tbody>
                  {sourceStats.sources.map((s) => (
                    <tr key={s.source} className="border-b last:border-0">
                      <td className="py-2 font-medium">{s.source}</td>
                      <td className="text-right py-2">{s.total}</td>
                      <td className="text-right py-2">
                        {s.keepRate != null ? (
                          <span className={cn(
                            'font-medium',
                            s.keepRate >= 80 ? 'text-green-600 dark:text-green-400' :
                            s.keepRate >= 60 ? 'text-yellow-600 dark:text-yellow-400' :
                            'text-red-600 dark:text-red-400'
                          )}>
                            {s.keepRate}%
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                      <td className="text-right py-2">
                        {s.fetchRate != null ? (
                          <span className={cn(
                            'font-medium',
                            s.fetchRate >= 80 ? 'text-green-600 dark:text-green-400' :
                            s.fetchRate >= 60 ? 'text-yellow-600 dark:text-yellow-400' :
                            'text-red-600 dark:text-red-400'
                          )}>
                            {s.fetchRate}%
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                      <td className="text-right py-2">
                        {s.avgEntityCount != null ? s.avgEntityCount.toFixed(1) : '-'}
                      </td>
                      <td className="text-right py-2">
                        {s.sentimentDistribution ? (
                          <div className="flex items-center justify-end gap-1.5">
                            {(s.sentimentDistribution.bullish ?? 0) > 0 && (
                              <Badge variant="outline" className="text-green-600 border-green-300 dark:border-green-700 text-xs px-1.5 py-0">
                                +{s.sentimentDistribution.bullish}
                              </Badge>
                            )}
                            {(s.sentimentDistribution.bearish ?? 0) > 0 && (
                              <Badge variant="outline" className="text-red-600 border-red-300 dark:border-red-700 text-xs px-1.5 py-0">
                                -{s.sentimentDistribution.bearish}
                              </Badge>
                            )}
                            {(s.sentimentDistribution.neutral ?? 0) > 0 && (
                              <Badge variant="outline" className="text-muted-foreground text-xs px-1.5 py-0">
                                {s.sentimentDistribution.neutral}
                              </Badge>
                            )}
                          </div>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
