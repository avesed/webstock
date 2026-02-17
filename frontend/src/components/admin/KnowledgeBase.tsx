import { useCallback, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Database, Newspaper, FileText, BookOpen, BarChart3, Loader2, RefreshCw, AlertCircle, AlertTriangle } from 'lucide-react'

import { adminApi, type KnowledgeBaseStatsResponse } from '@/api/admin'
import { getErrorMessage } from '@/api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useToast } from '@/hooks/useToast'

// Dynamic i18n key helper -- bypasses strict key checking for runtime-constructed keys
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const td = (t: (k: any, opts?: any) => string, key: string, opts?: Record<string, unknown>): string =>
  opts ? t(key, opts) : t(key)

// ── Types ──────────────────────────────────────

// Shared type from API client — single source of truth
type KnowledgeBaseStats = KnowledgeBaseStatsResponse

// Progress types referenced by sub-components
type StockProfileProgress = NonNullable<KnowledgeBaseStats['progress']['stockProfile']>

// ── Action Definition ──────────────────────────

interface ActionDef {
  key: string
  label: string
  fn: () => Promise<unknown>
  variant?: 'outline' | 'destructive' | 'default'
}

// Fast poll duration after user-initiated action (15s of 3s polling)
const FAST_POLL_DURATION_MS = 15_000

// ── Sub-components ─────────────────────────────

function ProgressBar({ percent, label }: { percent: number; label: string }) {
  const clampedPercent = Math.min(100, Math.max(0, percent))
  return (
    <div className="space-y-1">
      <div
        className="h-2 rounded-full bg-primary/20 overflow-hidden"
        role="progressbar"
        aria-valuenow={clampedPercent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label}
      >
        <div
          className="h-full bg-primary rounded-full transition-all duration-300"
          style={{ width: `${clampedPercent}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  )
}

function EmbeddingCard({
  title,
  description,
  icon,
  count,
  lastUpdated,
  model,
  failedCount,
  progress,
  actions,
  actionLoading,
}: {
  title: string
  description: string
  icon: React.ReactNode
  count: number
  lastUpdated: string | null
  model: string | null
  failedCount?: number
  progress: StockProfileProgress | null
  actions: ActionDef[]
  actionLoading: string | null
}) {
  const { t } = useTranslation('admin')
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          {icon}
          {title}
          {(failedCount ?? 0) > 0 && (
            <Badge variant="destructive" className="ml-auto">
              <AlertCircle className="h-3 w-3 mr-1" />
              {td(t, 'knowledge.failed', { count: failedCount })}
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs">{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {/* Stats */}
          <div className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <span className="text-muted-foreground">{t('knowledge.embeddings')}</span>{' '}
              <span className="font-medium">{count.toLocaleString()}</span>
            </div>
            <div>
              <span className="text-muted-foreground">{t('knowledge.model')}</span>{' '}
              <span className="font-mono text-xs">{model || '-'}</span>
            </div>
            {lastUpdated && (
              <div className="col-span-2 text-muted-foreground text-xs">
                {t('knowledge.lastUpdated')}: {new Date(lastUpdated).toLocaleString()}
              </div>
            )}
            {!lastUpdated && count === 0 && (
              <div className="col-span-2 text-muted-foreground text-xs italic">
                {t('knowledge.noData')}
              </div>
            )}
          </div>

          {/* Progress bar (shown when active) */}
          {progress && (
            <ProgressBar
              percent={progress.percent}
              label={`${progress.phase} ${progress.current}/${progress.total}`}
            />
          )}

          {/* Action buttons */}
          <div className="flex flex-wrap gap-2">
            {actions.map((action) => (
              <Button
                key={action.key}
                size="sm"
                variant={action.variant ?? 'outline'}
                disabled={actionLoading !== null}
                onClick={() => void action.fn()}
              >
                {actionLoading === action.key ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : null}
                {action.label}
              </Button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function DailyBarsCard({
  dailyBars,
  progress,
  actionLoading,
  onAction,
}: {
  dailyBars: KnowledgeBaseStats['dailyBars'] | undefined
  progress: KnowledgeBaseStats['progress']['dailyBars'] | undefined
  actionLoading: string | null
  onAction: (key: string, action: () => Promise<unknown>, confirmMsg?: string) => void
}) {
  const { t } = useTranslation('admin')
  const markets = ['cn', 'us', 'hk', 'metal'] as const

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <BarChart3 className="h-4 w-4" />
              {t('knowledge.dailyBars')}
            </CardTitle>
            <CardDescription className="text-xs mt-1">
              {t('knowledge.dailyBarsDesc')}
            </CardDescription>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={actionLoading !== null}
            onClick={() => onAction('collect-all', () => adminApi.collectAllDailyBars())}
          >
            {actionLoading === 'collect-all' ? (
              <Loader2 className="h-3 w-3 animate-spin mr-1" />
            ) : (
              <RefreshCw className="h-3 w-3 mr-1" />
            )}
            {t('knowledge.collectAll')}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {markets.map((market) => {
            const stats = dailyBars?.[market]
            const prog = progress?.[market]
            return (
              <div key={market} className="space-y-2 rounded-md border p-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium text-sm">
                    {td(t, `knowledge.market_${market}`)}
                  </span>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    disabled={actionLoading !== null}
                    onClick={() =>
                      onAction(`collect-${market}`, () => adminApi.collectDailyBars(market))
                    }
                  >
                    {actionLoading === `collect-${market}` ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      t('knowledge.collect')
                    )}
                  </Button>
                </div>

                {stats ? (
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <div>
                      <span className="font-medium text-foreground">
                        {stats.count.toLocaleString()}
                      </span>{' '}
                      {t('knowledge.bars')}
                      {' / '}
                      <span className="font-medium text-foreground">
                        {stats.symbolCount.toLocaleString()}
                      </span>{' '}
                      {t('knowledge.symbols')}
                    </div>
                    {stats.firstDate && stats.lastDate && (
                      <div>
                        {td(t, 'knowledge.dateRange', {
                          from: stats.firstDate,
                          to: stats.lastDate,
                        })}
                      </div>
                    )}
                    {!stats.firstDate && stats.count === 0 && (
                      <div className="italic">{t('knowledge.noData')}</div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-muted-foreground italic">{t('knowledge.noData')}</div>
                )}

                {prog && (
                  <ProgressBar
                    percent={prog.percent}
                    label={`${prog.symbolsDone}/${prog.symbolsTotal} (+${prog.newBars})`}
                  />
                )}
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      <div>
        <Skeleton className="h-7 w-48 mb-2" />
        <Skeleton className="h-4 w-96" />
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i}>
            <CardHeader className="pb-2">
              <Skeleton className="h-5 w-32" />
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <Skeleton className="h-4 w-20" />
                  <Skeleton className="h-4 w-24" />
                </div>
                <div className="flex gap-2">
                  <Skeleton className="h-8 w-16" />
                  <Skeleton className="h-8 w-16" />
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
      <Card>
        <CardHeader className="pb-2">
          <Skeleton className="h-5 w-40" />
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-md" />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function ErrorState({ onRetry }: { onRetry: () => void }) {
  const { t } = useTranslation('admin')
  return (
    <div className="flex flex-col items-center justify-center py-12 space-y-4">
      <AlertTriangle className="h-10 w-10 text-destructive" />
      <p className="text-muted-foreground">{t('knowledge.error')}</p>
      <Button variant="outline" onClick={onRetry}>
        <RefreshCw className="h-4 w-4 mr-2" />
        {t('knowledge.collect')}
      </Button>
    </div>
  )
}

// ── Main Component ─────────────────────────────

export default function KnowledgeBase() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()

  // Fast-poll timer: temporarily force 3s polling after user action
  const fastPollUntilRef = useRef(0)

  const { data: stats, isLoading, isError, refetch } = useQuery<KnowledgeBaseStats>({
    queryKey: ['admin', 'knowledge-base-stats'],
    queryFn: () => adminApi.getKnowledgeBaseStats(),
    refetchInterval: (query) => {
      // Force fast poll after user action
      if (Date.now() < fastPollUntilRef.current) return 3000
      const progress = query.state.data?.progress
      const hasActive =
        progress?.stockProfile != null ||
        Object.values(progress?.dailyBars ?? {}).some((v) => v != null)
      return hasActive ? 3000 : 30000
    },
  })

  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const handleAction = useCallback(async (
    key: string,
    action: () => Promise<unknown>,
    confirmMsg?: string
  ) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return
    setActionLoading(key)
    try {
      const result = (await action()) as { message?: string } | undefined
      toast({
        title: result?.message ?? t('knowledge.actionSuccess'),
      })
      // Temporarily speed up polling to catch Redis progress quickly
      fastPollUntilRef.current = Date.now() + FAST_POLL_DURATION_MS
      // Invalidate to refresh stats
      void queryClient.invalidateQueries({ queryKey: ['admin', 'knowledge-base-stats'] })
    } catch (error) {
      toast({
        title: t('knowledge.error'),
        description: getErrorMessage(error),
        variant: 'destructive',
      })
    } finally {
      setActionLoading(null)
    }
  }, [queryClient, t, toast])

  if (isLoading) {
    return <LoadingSkeleton />
  }

  if (isError) {
    return <ErrorState onRetry={() => void refetch()} />
  }

  const embeddings = stats?.embeddings
  const progress = stats?.progress

  // ── Build action lists per KB ────────────────

  const stockProfileActions: ActionDef[] = [
    {
      key: 'rebuild-stock_profile',
      label: t('knowledge.rebuild'),
      fn: () =>
        handleAction(
          'rebuild-stock_profile',
          () => adminApi.rebuildKnowledgeBase('stock_profile'),
          td(t, 'knowledge.rebuildConfirm', { type: t('knowledge.stockProfile') })
        ),
      variant: 'outline',
    },
    {
      key: 'sync-stock_profile',
      label: t('knowledge.syncConcepts'),
      fn: () =>
        handleAction(
          'sync-stock_profile',
          () => adminApi.rebuildKnowledgeBase('stock_profile_sync'),
        ),
      variant: 'outline',
    },
  ]

  const newsActions: ActionDef[] = [
    ...(embeddings?.news?.failedCount
      ? [
          {
            key: 'retry-news',
            label: td(t, 'knowledge.retryFailed', { count: embeddings.news.failedCount }),
            fn: () => handleAction('retry-news', () => adminApi.retryFailedEmbeddings('news')),
            variant: 'outline' as const,
          },
        ]
      : []),
    {
      key: 'rebuild-news',
      label: t('knowledge.rebuild'),
      fn: () =>
        handleAction(
          'rebuild-news',
          () => adminApi.rebuildKnowledgeBase('news'),
          td(t, 'knowledge.rebuildConfirm', { type: t('knowledge.news') })
        ),
      variant: 'outline',
    },
    {
      key: 'clear-news',
      label: t('knowledge.clear'),
      fn: () =>
        handleAction(
          'clear-news',
          () => adminApi.clearKnowledgeBase('news'),
          td(t, 'knowledge.clearConfirm', { type: t('knowledge.news') })
        ),
      variant: 'destructive',
    },
  ]

  const analysisActions: ActionDef[] = [
    {
      key: 'rebuild-analysis',
      label: t('knowledge.rebuild'),
      fn: () =>
        handleAction(
          'rebuild-analysis',
          () => adminApi.rebuildKnowledgeBase('analysis'),
          td(t, 'knowledge.rebuildConfirm', { type: t('knowledge.analysis') })
        ),
      variant: 'outline',
    },
    {
      key: 'clear-analysis',
      label: t('knowledge.clear'),
      fn: () =>
        handleAction(
          'clear-analysis',
          () => adminApi.clearKnowledgeBase('analysis'),
          td(t, 'knowledge.clearConfirm', { type: t('knowledge.analysis') })
        ),
      variant: 'destructive',
    },
  ]

  const reportActions: ActionDef[] = [
    ...(embeddings?.report?.failedCount
      ? [
          {
            key: 'retry-report',
            label: td(t, 'knowledge.retryFailed', { count: embeddings.report.failedCount }),
            fn: () => handleAction('retry-report', () => adminApi.retryFailedEmbeddings('report')),
            variant: 'outline' as const,
          },
        ]
      : []),
    {
      key: 'rebuild-report',
      label: t('knowledge.rebuild'),
      fn: () =>
        handleAction(
          'rebuild-report',
          () => adminApi.rebuildKnowledgeBase('report'),
          td(t, 'knowledge.rebuildConfirm', { type: t('knowledge.report') })
        ),
      variant: 'outline',
    },
    {
      key: 'clear-report',
      label: t('knowledge.clear'),
      fn: () =>
        handleAction(
          'clear-report',
          () => adminApi.clearKnowledgeBase('report'),
          td(t, 'knowledge.clearConfirm', { type: t('knowledge.report') })
        ),
      variant: 'destructive',
    },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold tracking-tight">{t('knowledge.title')}</h2>
        <p className="text-muted-foreground text-sm">{t('knowledge.description')}</p>
      </div>

      {/* Embedding Knowledge Bases: 2x2 grid */}
      <div className="grid gap-4 md:grid-cols-2">
        <EmbeddingCard
          title={t('knowledge.stockProfile')}
          description={t('knowledge.stockProfileDesc')}
          icon={<Database className="h-4 w-4" />}
          count={embeddings?.stock_profile?.count ?? 0}
          lastUpdated={embeddings?.stock_profile?.lastUpdated ?? null}
          model={embeddings?.stock_profile?.model ?? null}
          progress={progress?.stockProfile ?? null}
          actions={stockProfileActions}
          actionLoading={actionLoading}
        />
        <EmbeddingCard
          title={t('knowledge.news')}
          description={t('knowledge.newsDesc')}
          icon={<Newspaper className="h-4 w-4" />}
          count={embeddings?.news?.count ?? 0}
          lastUpdated={embeddings?.news?.lastUpdated ?? null}
          model={embeddings?.news?.model ?? null}
          failedCount={embeddings?.news?.failedCount ?? 0}
          progress={null}
          actions={newsActions}
          actionLoading={actionLoading}
        />
        <EmbeddingCard
          title={t('knowledge.analysis')}
          description={t('knowledge.analysisDesc')}
          icon={<BookOpen className="h-4 w-4" />}
          count={embeddings?.analysis?.count ?? 0}
          lastUpdated={embeddings?.analysis?.lastUpdated ?? null}
          model={embeddings?.analysis?.model ?? null}
          progress={null}
          actions={analysisActions}
          actionLoading={actionLoading}
        />
        <EmbeddingCard
          title={t('knowledge.report')}
          description={t('knowledge.reportDesc')}
          icon={<FileText className="h-4 w-4" />}
          count={embeddings?.report?.count ?? 0}
          lastUpdated={embeddings?.report?.lastUpdated ?? null}
          model={embeddings?.report?.model ?? null}
          failedCount={embeddings?.report?.failedCount ?? 0}
          progress={null}
          actions={reportActions}
          actionLoading={actionLoading}
        />
      </div>

      {/* Daily Bars: full-width card */}
      <DailyBarsCard
        dailyBars={stats?.dailyBars}
        progress={progress?.dailyBars}
        actionLoading={actionLoading}
        onAction={handleAction}
      />
    </div>
  )
}
