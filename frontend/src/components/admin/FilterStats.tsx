import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle, Filter, Coins, TrendingUp, TrendingDown, Play, Loader2, Clock, Vote } from 'lucide-react'

import { adminApi, FilterStats as FilterStatsType, MonitorStatus, SourceStats } from '@/api/admin'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
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

function formatNumber(n: number): string {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return n.toString()
}

function formatCost(cost: number): string {
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
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

  const { data: stats, isLoading, error, refetch } = useQuery<FilterStatsType>({
    queryKey: ['admin', 'filter-stats'],
    queryFn: () => adminApi.getFilterStats(7),
    refetchInterval: 60000,
  })

  const { data: sourceStats, isLoading: sourceLoading } = useQuery<SourceStats>({
    queryKey: ['admin', 'source-stats'],
    queryFn: () => adminApi.getSourceStats(7),
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

  // Defensive defaults for API response structure
  const defaultTokenUsage = { inputTokens: 0, outputTokens: 0, totalTokens: 0, estimatedCostUsd: 0 }

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

  const initialTokens = stats.tokens?.initialFilter ?? defaultTokenUsage
  const deepTokens = stats.tokens?.deepFilter ?? defaultTokenUsage
  const totalTokens = stats.tokens?.total ?? defaultTokenUsage
  const strictTokens = stats.tokens?.initialStrict ?? null
  const permissiveTokens = stats.tokens?.initialPermissive ?? null

  const voting = stats.counts?.voting ?? null
  const votingTotal = voting
    ? (voting.unanimousSkip + voting.majoritySkip + voting.majorityPass + voting.unanimousPass)
    : 0

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
            {!isRunning && countdown && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Clock className="h-4 w-4" />
                <span>{t('filter.nextRun')}: {countdown}</span>
              </div>
            )}
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
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title={t('filter.initialFilter')}
          value={initialFilter.total}
          subtitle={`${rates.initialPassRate}% ${t('filter.passRate')}`}
          icon={<Filter className="h-4 w-4 text-muted-foreground" />}
        />
        <StatCard
          title={t('filter.deepFilter')}
          value={deepFilter.total}
          subtitle={`${rates.deepKeepRate}% ${t('filter.passRate')}`}
          icon={<CheckCircle className="h-4 w-4 text-muted-foreground" />}
        />
        <StatCard
          title={t('filter.totalTokens')}
          value={formatNumber(totalTokens.totalTokens)}
          subtitle={`${formatNumber(totalTokens.inputTokens)} in / ${formatNumber(totalTokens.outputTokens)} out`}
          icon={<Coins className="h-4 w-4 text-muted-foreground" />}
        />
        <StatCard
          title={t('filter.estimatedCost')}
          value={formatCost(totalTokens.estimatedCostUsd)}
          subtitle={`${stats.periodDays ?? 0} ${t('filter.days')}`}
          icon={<span className="text-muted-foreground">$</span>}
        />
      </div>

      {/* Detailed Stats */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Initial Filter Breakdown */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t('filter.initialFilter')}</CardTitle>
            <CardDescription>{t('filter.description')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
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

            {/* Voting stats (multi-agent) */}
            {voting && votingTotal > 0 && (
              <div className="pt-4 border-t">
                <p className="text-sm font-medium mb-2 flex items-center gap-1.5">
                  <Vote className="h-3.5 w-3.5" />
                  {t('filter.votingStats')}
                </p>
                <div className="grid grid-cols-4 gap-2 text-center">
                  <div>
                    <p className="text-lg font-bold text-red-600 dark:text-red-400">{voting.unanimousSkip}</p>
                    <p className="text-xs text-muted-foreground">{t('filter.unanimousSkip')}</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-orange-600 dark:text-orange-400">{voting.majoritySkip}</p>
                    <p className="text-xs text-muted-foreground">{t('filter.majoritySkip')}</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-blue-600 dark:text-blue-400">{voting.majorityPass}</p>
                    <p className="text-xs text-muted-foreground">{t('filter.majorityPass')}</p>
                  </div>
                  <div>
                    <p className="text-lg font-bold text-green-600 dark:text-green-400">{voting.unanimousPass}</p>
                    <p className="text-xs text-muted-foreground">{t('filter.unanimousPass')}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Token usage for initial filter */}
            <div className="pt-4 border-t">
              <p className="text-sm text-muted-foreground mb-2">{t('filter.inputTokens')} / {t('filter.outputTokens')}</p>

              {/* Per-agent breakdown when available */}
              {(strictTokens || permissiveTokens) ? (
                <div className="space-y-2">
                  <div className="text-sm">
                    <span className="text-muted-foreground">{t('filter.strictAgent')}:</span>{' '}
                    <span className="font-medium">{formatNumber(strictTokens?.inputTokens ?? 0)}</span>
                    <span className="text-muted-foreground"> in / </span>
                    <span className="font-medium">{formatNumber(strictTokens?.outputTokens ?? 0)}</span>
                    <span className="text-muted-foreground"> out</span>
                  </div>
                  <div className="text-sm">
                    <span className="text-muted-foreground">{t('filter.moderateAgent')}:</span>{' '}
                    <span className="font-medium">{formatNumber(initialTokens.inputTokens)}</span>
                    <span className="text-muted-foreground"> in / </span>
                    <span className="font-medium">{formatNumber(initialTokens.outputTokens)}</span>
                    <span className="text-muted-foreground"> out</span>
                  </div>
                  <div className="text-sm">
                    <span className="text-muted-foreground">{t('filter.permissiveAgent')}:</span>{' '}
                    <span className="font-medium">{formatNumber(permissiveTokens?.inputTokens ?? 0)}</span>
                    <span className="text-muted-foreground"> in / </span>
                    <span className="font-medium">{formatNumber(permissiveTokens?.outputTokens ?? 0)}</span>
                    <span className="text-muted-foreground"> out</span>
                  </div>
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-muted-foreground">In:</span>{' '}
                    <span className="font-medium">{formatNumber(initialTokens.inputTokens)}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Out:</span>{' '}
                    <span className="font-medium">{formatNumber(initialTokens.outputTokens)}</span>
                  </div>
                </div>
              )}
              <p className="text-xs text-muted-foreground mt-1">
                {(strictTokens || permissiveTokens)
                  ? `${t('filter.allAgentsCost')}: ${formatCost(initialTokens.estimatedCostUsd + (strictTokens?.estimatedCostUsd ?? 0) + (permissiveTokens?.estimatedCostUsd ?? 0))}`
                  : `${t('filter.estimatedCost')}: ${formatCost(initialTokens.estimatedCostUsd)}`
                }
              </p>
            </div>
          </CardContent>
        </Card>

        {/* Deep Filter Breakdown */}
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

            {/* Token usage for deep filter */}
            <div className="pt-4 border-t">
              <p className="text-sm text-muted-foreground mb-2">{t('filter.inputTokens')} / {t('filter.outputTokens')}</p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-muted-foreground">In:</span>{' '}
                  <span className="font-medium">{formatNumber(deepTokens.inputTokens)}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">Out:</span>{' '}
                  <span className="font-medium">{formatNumber(deepTokens.outputTokens)}</span>
                </div>
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {t('filter.estimatedCost')}: {formatCost(deepTokens.estimatedCostUsd)}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Error Stats */}
      {(errors.filterError > 0 || errors.embeddingError > 0) && (
        <Card className="border-red-200 dark:border-red-800">
          <CardHeader>
            <CardTitle className="text-base text-red-600 dark:text-red-400">
              {t('filter.errors')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-4 text-sm">
              <div>
                <span className="text-muted-foreground">{t('filter.filterError')}:</span>{' '}
                <span className="font-medium text-red-600">{errors.filterError}</span>
                <span className="text-muted-foreground ml-1">({rates.filterErrorRate}%)</span>
              </div>
              <div>
                <span className="text-muted-foreground">{t('filter.embeddingError')}:</span>{' '}
                <span className="font-medium text-red-600">{errors.embeddingError}</span>
                <span className="text-muted-foreground ml-1">({rates.embeddingErrorRate}%)</span>
              </div>
            </div>
          </CardContent>
        </Card>
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
                    <th className="text-right py-2 font-medium">{t('filter.embedRateColumn')}</th>
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
                        {s.embedRate != null ? (
                          <span className={cn(
                            'font-medium',
                            s.embedRate >= 80 ? 'text-green-600 dark:text-green-400' :
                            s.embedRate >= 60 ? 'text-yellow-600 dark:text-yellow-400' :
                            'text-red-600 dark:text-red-400'
                          )}>
                            {s.embedRate}%
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
