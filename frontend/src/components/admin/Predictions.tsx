import { Fragment, useState, useMemo, useCallback, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  TrendingUp, Play, RefreshCw, Loader2, ChevronDown, ChevronRight,
  BarChart3, Target, Brain, AlertCircle, CheckCircle2, XCircle, Activity,
  ArrowUpRight, ArrowDownRight, Minus, PieChart,
} from 'lucide-react'

import { predictionsApi } from '@/api/predictions'
import type {
  PredictionResult, PredictionModel, PredictionTask,
  PredictionStatusItem,
} from '@/api/predictions'
import { getErrorMessage } from '@/api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { useToast } from '@/hooks/useToast'
import { cn } from '@/lib/utils'

import RDAgent from './RDAgent'
import BacktestPanel from './predictions/BacktestPanel'

// Dynamic i18n key helper
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const td = (t: (k: any, opts?: any) => string, key: string, opts?: Record<string, unknown>): string =>
  opts ? t(key, opts) : t(key)

const MARKETS = ['cn', 'us', 'hk'] as const
type MarketKey = typeof MARKETS[number]

// ── Helpers ────────────────────────────────────

function formatIc(v: number | null): string {
  if (v === null || v === undefined) return '-'
  return v.toFixed(4)
}

function formatPercent(v: number | null): string {
  if (v === null || v === undefined) return '-'
  return `${(v * 100).toFixed(1)}%`
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function directionBadge(dir: PredictionResult['predictedDirection'], t: (k: any, opts?: any) => string) {
  const map = {
    up: { label: td(t, 'predictions.direction_up'), cls: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200' },
    down: { label: td(t, 'predictions.direction_down'), cls: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200' },
    sideways: { label: td(t, 'predictions.direction_flat'), cls: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200' },
  }
  const m = map[dir]
  return <Badge className={m.cls}>{m.label}</Badge>
}

// ── Status Overview ────────────────────────────

function StatusOverview({ status }: { status: Record<string, PredictionStatusItem> }) {
  const { t } = useTranslation('admin')

  return (
    <div className="grid gap-4 md:grid-cols-3">
      {MARKETS.map(mkt => {
        const s = status[mkt]
        const latestModel = s?.models?.[0]
        const latestPrediction = s?.latestPredictions?.[0]
        const lastIc = latestModel?.ic ?? null
        const lastIcir = latestModel?.icir ?? null
        const ensembleSize = latestModel?.ensembleSize
        const foldIcs = latestModel?.foldIcs
        return (
          <Card key={mkt}>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <TrendingUp className="h-4 w-4" />
                {td(t, `predictions.market_${mkt}`)}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              {s?.error ? (
                <p className="text-xs text-destructive">{s.error}</p>
              ) : (
                <>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{td(t, 'predictions.lastPrediction')}</span>
                    <span>{latestPrediction?.predictionDate ?? latestModel?.modelDate ?? '-'}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{td(t, 'predictions.modelIc')}</span>
                    <span className={cn(
                      lastIc != null && lastIc > 0.03 && 'text-green-600 dark:text-green-400',
                      lastIc != null && lastIc < 0.01 && 'text-red-600 dark:text-red-400',
                    )}>
                      {formatIc(lastIc)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">ICIR</span>
                    <span className={cn(
                      lastIcir != null && lastIcir > 0.5 && 'text-green-600 dark:text-green-400',
                      lastIcir != null && lastIcir < 0.1 && 'text-red-600 dark:text-red-400',
                    )}>
                      {formatIc(lastIcir)}
                    </span>
                  </div>
                  {ensembleSize != null && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">{td(t, 'predictions.ensemble')}</span>
                      <span>{ensembleSize} {td(t, 'predictions.members')}</span>
                    </div>
                  )}
                  {foldIcs && foldIcs.length > 0 && (
                    <div className="flex justify-between items-center">
                      <span className="text-muted-foreground">{td(t, 'predictions.foldIcs')}</span>
                      <div className="flex gap-1">
                        {foldIcs.map((ic, i) => (
                          <Badge
                            key={i}
                            variant="outline"
                            className={cn(
                              'text-[10px] px-1 py-0',
                              ic > 0.02 && 'border-green-500 text-green-600 dark:text-green-400',
                              ic < 0 && 'border-red-500 text-red-600 dark:text-red-400',
                            )}
                          >
                            {ic.toFixed(3)}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">{td(t, 'predictions.models')}</span>
                    <span>{s?.models?.length ?? 0}</span>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

// ── Prediction Results Table ───────────────────

function PredictionResultsSection() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('cn')

  const { data: predictions, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'latest', selectedMarket],
    queryFn: () => predictionsApi.getLatestPredictions(selectedMarket),
    staleTime: 5 * 60_000,
  })

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Target className="h-4 w-4" />
              {td(t, 'predictions.latestResults')}
            </CardTitle>
            <CardDescription className="text-xs">{td(t, 'predictions.latestResultsDesc')}</CardDescription>
          </div>
          <div className="flex gap-1">
            {MARKETS.map(mkt => (
              <Button
                key={mkt}
                variant={selectedMarket === mkt ? 'default' : 'outline'}
                size="sm"
                onClick={() => setSelectedMarket(mkt)}
              >
                {mkt.toUpperCase()}
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        ) : !predictions?.length ? (
          <p className="text-sm text-muted-foreground py-4 text-center">{td(t, 'predictions.noResults')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="text-left py-2 font-medium">{td(t, 'predictions.symbol')}</th>
                  <th className="text-right py-2 font-medium">{td(t, 'predictions.score')}</th>
                  <th className="text-right py-2 font-medium">{td(t, 'predictions.rank')}</th>
                  <th className="text-center py-2 font-medium">{td(t, 'predictions.direction')}</th>
                  <th className="text-right py-2 font-medium">{td(t, 'predictions.actualReturn')}</th>
                </tr>
              </thead>
              <tbody>
                {predictions.map(p => (
                  <tr key={p.symbol} className="border-b last:border-0 hover:bg-muted/50">
                    <td className="py-2 font-mono text-xs">{p.symbol}</td>
                    <td className="py-2 text-right">{p.predictedScore.toFixed(4)}</td>
                    <td className="py-2 text-right">{formatPercent(p.percentileRank)}</td>
                    <td className="py-2 text-center">{directionBadge(p.predictedDirection, t)}</td>
                    <td className="py-2 text-right">
                      {p.actualReturn != null ? (
                        <span className={p.actualReturn >= 0 ? 'text-green-600' : 'text-red-600'}>
                          {(p.actualReturn * 100).toFixed(2)}%
                        </span>
                      ) : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Model History ──────────────────────────────

function ModelHistorySection() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [expandedModelId, setExpandedModelId] = useState<string | null>(null)

  const { data: models, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'models'],
    queryFn: () => predictionsApi.getModels(),
    staleTime: 5 * 60_000,
  })

  const { data: featureImportance, isFetching: fiLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'feature-importance', expandedModelId],
    queryFn: () => predictionsApi.getFeatureImportance(expandedModelId!),
    enabled: !!expandedModelId,
    staleTime: 10 * 60_000,
  })

  const qualityMutation = useMutation({
    mutationFn: ({ modelId, passed }: { modelId: string; passed: boolean }) =>
      predictionsApi.updateModelQuality(modelId, passed),
    onSuccess: () => {
      toast({ title: td(t, 'predictions.qualityUpdated') })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'predictions', 'models'] })
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.qualityUpdateError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const toggleModelExpand = (modelId: string) => {
    setExpandedModelId(prev => prev === modelId ? null : modelId)
  }

  return (
    <Card>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setExpanded(prev => !prev)}>
        <CardTitle className="flex items-center gap-2 text-base">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <BarChart3 className="h-4 w-4" />
          {td(t, 'predictions.modelHistory')}
          {models?.length ? (
            <Badge variant="secondary" className="ml-2">{models.length}</Badge>
          ) : null}
        </CardTitle>
        <CardDescription className="text-xs">{td(t, 'predictions.modelHistoryDesc')}</CardDescription>
      </CardHeader>
      {expanded && (
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </div>
          ) : !models?.length ? (
            <p className="text-sm text-muted-foreground py-4 text-center">{td(t, 'predictions.noModels')}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 font-medium">{td(t, 'predictions.date')}</th>
                    <th className="text-left py-2 font-medium">{td(t, 'predictions.marketLabel')}</th>
                    <th className="text-right py-2 font-medium">IC</th>
                    <th className="text-right py-2 font-medium">ICIR</th>
                    <th className="text-right py-2 font-medium">NDCG</th>
                    <th className="text-center py-2 font-medium">{td(t, 'predictions.quality')}</th>
                    <th className="text-right py-2 font-medium">{td(t, 'predictions.features')}</th>
                    <th className="text-right py-2 font-medium">{td(t, 'predictions.symbols')}</th>
                    <th className="py-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m: PredictionModel) => (
                    <Fragment key={m.id}>
                      <tr
                        className="border-b last:border-0 hover:bg-muted/50 cursor-pointer"
                        tabIndex={0}
                        role="button"
                        aria-expanded={expandedModelId === m.id}
                        onClick={() => toggleModelExpand(m.id)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault()
                            toggleModelExpand(m.id)
                          }
                        }}
                      >
                        <td className="py-2 text-xs">{m.modelDate}</td>
                        <td className="py-2">{m.market.toUpperCase()}</td>
                        <td className={cn(
                          'py-2 text-right',
                          m.ic != null && m.ic > 0.03 && 'text-green-600 dark:text-green-400',
                          m.ic != null && m.ic < 0.01 && 'text-red-600 dark:text-red-400',
                        )}>
                          {formatIc(m.ic)}
                        </td>
                        <td className="py-2 text-right">{formatIc(m.icir)}</td>
                        <td className="py-2 text-right">{formatIc(m.ndcg)}</td>
                        <td className="py-2 text-center">
                          {m.qualityPassed ? (
                            <CheckCircle2 className="h-4 w-4 text-green-600 dark:text-green-400 inline" />
                          ) : (
                            <XCircle className="h-4 w-4 text-red-600 dark:text-red-400 inline" />
                          )}
                        </td>
                        <td className="py-2 text-right">{m.featureCount}</td>
                        <td className="py-2 text-right">{m.symbolCount}</td>
                        <td className="py-2 text-right">
                          {expandedModelId === m.id ? (
                            <ChevronDown className="h-3 w-3 inline text-muted-foreground" />
                          ) : (
                            <ChevronRight className="h-3 w-3 inline text-muted-foreground" />
                          )}
                        </td>
                      </tr>
                      {expandedModelId === m.id && (
                        <tr>
                          <td colSpan={9} className="py-3 px-4 bg-muted/30">
                            <div className="space-y-3">
                              {/* Quality override buttons */}
                              <div className="flex items-center gap-2">
                                <span className="text-xs text-muted-foreground">{td(t, 'predictions.qualityOverride')}:</span>
                                <Button
                                  size="sm"
                                  variant={m.qualityPassed ? 'default' : 'outline'}
                                  className="h-6 text-xs"
                                  disabled={qualityMutation.isPending}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    qualityMutation.mutate({ modelId: m.id, passed: true })
                                  }}
                                >
                                  <CheckCircle2 className="h-3 w-3 mr-1" />
                                  {td(t, 'predictions.approve')}
                                </Button>
                                <Button
                                  size="sm"
                                  variant={!m.qualityPassed ? 'destructive' : 'outline'}
                                  className="h-6 text-xs"
                                  disabled={qualityMutation.isPending}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    qualityMutation.mutate({ modelId: m.id, passed: false })
                                  }}
                                >
                                  <XCircle className="h-3 w-3 mr-1" />
                                  {td(t, 'predictions.reject')}
                                </Button>
                              </div>
                              {/* Feature Importance */}
                              <div>
                                <p className="text-xs font-medium mb-2">{td(t, 'predictions.featureImportance')}</p>
                                {fiLoading ? (
                                  <Skeleton className="h-24 w-full" />
                                ) : featureImportance?.top30 && Object.keys(featureImportance.top30).length > 0 ? (
                                  <div className="space-y-1">
                                    {Object.entries(featureImportance.top30)
                                      .sort(([, a], [, b]) => b - a)
                                      .slice(0, 15)
                                      .map(([name, value]) => {
                                        const maxVal = Math.max(...Object.values(featureImportance.top30))
                                        const pct = maxVal > 0 ? (value / maxVal) * 100 : 0
                                        return (
                                          <div key={name} className="flex items-center gap-2 text-xs">
                                            <span className="w-36 truncate text-muted-foreground font-mono" title={name}>
                                              {name}
                                            </span>
                                            <div className="flex-1 h-3 bg-muted rounded-full overflow-hidden">
                                              <div
                                                className="h-full bg-primary/60 rounded-full"
                                                style={{ width: `${pct}%` }}
                                              />
                                            </div>
                                            <span className="w-16 text-right text-muted-foreground">
                                              {value.toFixed(1)}
                                            </span>
                                          </div>
                                        )
                                      })}
                                  </div>
                                ) : (
                                  <p className="text-xs text-muted-foreground">{td(t, 'predictions.noFeatureData')}</p>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

// ── Accuracy Section ───────────────────────────

function AccuracySection() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('cn')
  const [days, setDays] = useState(30)

  const { data: accuracy, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'accuracy', selectedMarket, days],
    queryFn: () => predictionsApi.getAccuracy(selectedMarket, days),
    staleTime: 10 * 60_000,
  })

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-base">{td(t, 'predictions.accuracy')}</CardTitle>
            <CardDescription className="text-xs">{td(t, 'predictions.accuracyDesc')}</CardDescription>
          </div>
          <div className="flex gap-1">
            {[7, 30, 90].map(d => (
              <Button
                key={d}
                variant={days === d ? 'default' : 'outline'}
                size="sm"
                onClick={() => setDays(d)}
              >
                {d}d
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex gap-1 mb-4">
          {MARKETS.map(mkt => (
            <Button
              key={mkt}
              variant={selectedMarket === mkt ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedMarket(mkt)}
            >
              {mkt.toUpperCase()}
            </Button>
          ))}
        </div>
        {isLoading ? (
          <Skeleton className="h-20 w-full" />
        ) : accuracy && accuracy.totalPredictions > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.totalPredictions')}</p>
              <p className="text-lg font-semibold">{accuracy.totalPredictions}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.directionAccuracy')}</p>
              <p className={cn(
                'text-lg font-semibold',
                accuracy.accuracy > 0.55 && 'text-green-600 dark:text-green-400',
                accuracy.accuracy < 0.45 && 'text-red-600 dark:text-red-400',
              )}>
                {formatPercent(accuracy.accuracy)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgIc')}</p>
              <p className="text-lg font-semibold">{formatIc(accuracy.avgIc)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgIcir')}</p>
              <p className="text-lg font-semibold">{formatIc(accuracy.avgIcir)}</p>
            </div>
          </div>
        ) : (
          <div className="text-center py-4 space-y-1">
            <p className="text-sm text-muted-foreground">{td(t, 'predictions.noAccuracyData')}</p>
            {accuracy && accuracy.pendingCount > 0 && (
              <p className="text-xs text-muted-foreground">
                {td(t, 'predictions.pendingVerification', { count: accuracy.pendingCount })}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Trigger Section ────────────────────────────

function TriggerSection() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [activeTasks, setActiveTasks] = useState<Record<string, PredictionTask | null>>({})
  const intervalsRef = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  // Cleanup all polling intervals on unmount
  useEffect(() => {
    const intervals = intervalsRef.current
    return () => {
      Object.values(intervals).forEach(clearInterval)
    }
  }, [])

  const triggerMutation = useMutation({
    mutationFn: ({ market, forceRetrain }: { market: string; forceRetrain: boolean }) =>
      predictionsApi.triggerPrediction(market, forceRetrain),
    onSuccess: (task, { market }) => {
      setActiveTasks(prev => ({ ...prev, [market]: task }))
      toast({ title: td(t, 'predictions.triggerSuccess') })
      // Start polling
      pollTask(market, task.taskId)
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.triggerError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const pollTask = useCallback((market: string, taskId: string) => {
    // Clear any existing interval for this market
    if (intervalsRef.current[market]) {
      clearInterval(intervalsRef.current[market])
    }
    const interval = setInterval(async () => {
      try {
        const status = await predictionsApi.getTaskStatus(taskId)
        setActiveTasks(prev => ({ ...prev, [market]: status }))
        if (status.status === 'completed' || status.status === 'failed') {
          clearInterval(interval)
          delete intervalsRef.current[market]
          if (status.status === 'completed') {
            void queryClient.invalidateQueries({ queryKey: ['admin', 'predictions'] })
          }
        }
      } catch {
        clearInterval(interval)
        delete intervalsRef.current[market]
        setActiveTasks(prev => ({ ...prev, [market]: null }))
      }
    }, 3000)
    intervalsRef.current[market] = interval
  }, [queryClient])

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Play className="h-4 w-4" />
          {td(t, 'predictions.manualTrigger')}
        </CardTitle>
        <CardDescription className="text-xs">{td(t, 'predictions.manualTriggerDesc')}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 md:grid-cols-3">
          {MARKETS.map(mkt => {
            const task = activeTasks[mkt]
            const isRunning = task && (task.status === 'pending' || task.status === 'training' || task.status === 'predicting')

            return (
              <div key={mkt} className="space-y-2 rounded-lg border p-3">
                <div className="flex items-center justify-between">
                  <span className="font-medium">{td(t, `predictions.market_${mkt}`)}</span>
                  {task && (
                    <Badge variant={task.status === 'completed' ? 'default' : task.status === 'failed' ? 'destructive' : 'secondary'}>
                      {td(t, `predictions.task_${task.status}`)}
                    </Badge>
                  )}
                </div>
                {isRunning && task.progress != null && (
                  <Progress value={task.progress} className="h-2" />
                )}
                {task?.message && (
                  <p className="text-xs text-muted-foreground">{task.message}</p>
                )}
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!isRunning || triggerMutation.isPending}
                    onClick={() => triggerMutation.mutate({ market: mkt, forceRetrain: false })}
                  >
                    {isRunning ? (
                      <Loader2 className="h-3 w-3 animate-spin mr-1" />
                    ) : (
                      <Play className="h-3 w-3 mr-1" />
                    )}
                    {td(t, 'predictions.predict')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!!isRunning || triggerMutation.isPending}
                    onClick={() => triggerMutation.mutate({ market: mkt, forceRetrain: true })}
                  >
                    <RefreshCw className="h-3 w-3 mr-1" />
                    {td(t, 'predictions.retrain')}
                  </Button>
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

// ── Signal Quality (IC Decay, Turnover, Sectors) ──

function SignalQualitySection() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('us')
  const [expanded, setExpanded] = useState(false)

  const { data: icDecay, isLoading: icLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'ic-decay', selectedMarket],
    queryFn: () => predictionsApi.getIcDecay(selectedMarket),
    staleTime: 10 * 60_000,
    enabled: expanded,
  })

  const { data: turnover, isLoading: turnoverLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'turnover', selectedMarket],
    queryFn: () => predictionsApi.getTurnover(selectedMarket),
    staleTime: 10 * 60_000,
    enabled: expanded,
  })

  const { data: sectors, isLoading: sectorsLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'sectors', selectedMarket],
    queryFn: () => predictionsApi.getSectors(selectedMarket),
    staleTime: 30 * 60_000,
    enabled: expanded,
  })

  const collectMutation = useMutation({
    mutationFn: (market: string) => predictionsApi.collectSectors(market),
    onSuccess: () => {
      toast({ title: td(t, 'predictions.sectorCollectStarted') })
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.sectorCollectError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const horizonEntries = icDecay?.horizons
    ? Object.entries(icDecay.horizons).sort(([a], [b]) => Number(a) - Number(b))
    : []

  return (
    <Card>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setExpanded(prev => !prev)}>
        <CardTitle className="flex items-center gap-2 text-base">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <Activity className="h-4 w-4" />
          {td(t, 'predictions.signalQuality')}
        </CardTitle>
        <CardDescription className="text-xs">{td(t, 'predictions.signalQualityDesc')}</CardDescription>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-6">
          {/* Market tabs */}
          <div className="flex gap-1">
            {MARKETS.map(mkt => (
              <Button
                key={mkt}
                variant={selectedMarket === mkt ? 'default' : 'outline'}
                size="sm"
                onClick={() => setSelectedMarket(mkt)}
              >
                {mkt.toUpperCase()}
              </Button>
            ))}
          </div>

          {/* Sector Coverage */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm font-medium">{td(t, 'predictions.sectorCoverage')}</p>
              <Button
                size="sm"
                variant="outline"
                className="h-7 text-xs"
                disabled={collectMutation.isPending}
                onClick={() => collectMutation.mutate(selectedMarket)}
              >
                <RefreshCw className={cn('h-3 w-3 mr-1', collectMutation.isPending && 'animate-spin')} />
                {td(t, 'predictions.collectSectors')}
              </Button>
            </div>
            {sectorsLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : sectors ? (
              <div className="space-y-2">
                <div className="flex gap-4 text-sm">
                  <div>
                    <span className="text-muted-foreground">{td(t, 'predictions.symbols')}: </span>
                    <span className="font-medium">{sectors.totalSymbols}</span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">{td(t, 'predictions.sectorCount')}: </span>
                    <span className="font-medium">{sectors.uniqueSectors}</span>
                  </div>
                </div>
                {Object.keys(sectors.sectorCounts).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(sectors.sectorCounts)
                      .sort(([, a], [, b]) => b - a)
                      .map(([name, count]) => (
                        <Badge key={name} variant="outline" className="text-[10px] py-0">
                          {name} ({count})
                        </Badge>
                      ))}
                  </div>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.noSectorData')}</p>
            )}
          </div>

          {/* IC Decay */}
          <div>
            <p className="text-sm font-medium mb-2">{td(t, 'predictions.icDecay')}</p>
            {icLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : horizonEntries.length > 0 ? (
              <div className="space-y-1.5">
                {horizonEntries.map(([horizon, data]) => {
                  const ic = data.avg_ic
                  const maxIc = Math.max(...horizonEntries.map(([, d]) => Math.abs(d.avg_ic)), 0.001)
                  const pct = Math.abs(ic) / maxIc * 100
                  return (
                    <div key={horizon} className="flex items-center gap-2 text-xs">
                      <span className="w-10 text-right text-muted-foreground">t+{horizon}</span>
                      <div className="flex-1 h-4 bg-muted rounded-full overflow-hidden">
                        <div
                          className={cn(
                            'h-full rounded-full',
                            ic > 0 ? 'bg-green-500/60' : 'bg-red-500/60',
                          )}
                          style={{ width: `${Math.min(pct, 100)}%` }}
                        />
                      </div>
                      <span className={cn(
                        'w-16 text-right font-mono',
                        ic > 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400',
                      )}>
                        {ic.toFixed(4)}
                      </span>
                      <span className="w-8 text-right text-muted-foreground">n={data.n_dates}</span>
                    </div>
                  )
                })}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.noIcDecayData')}</p>
            )}
          </div>

          {/* Turnover */}
          <div>
            <p className="text-sm font-medium mb-2">{td(t, 'predictions.turnover')}</p>
            {turnoverLoading ? (
              <Skeleton className="h-16 w-full" />
            ) : turnover && turnover.dataPoints > 0 ? (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgRankAutocorr')}</p>
                  <p className={cn(
                    'text-lg font-semibold',
                    turnover.summary.avgRankAutocorr != null && turnover.summary.avgRankAutocorr > 0.5 && 'text-green-600 dark:text-green-400',
                    turnover.summary.avgRankAutocorr != null && turnover.summary.avgRankAutocorr < 0.2 && 'text-red-600 dark:text-red-400',
                  )}>
                    {turnover.summary.avgRankAutocorr != null ? turnover.summary.avgRankAutocorr.toFixed(3) : '-'}
                  </p>
                  <p className="text-[10px] text-muted-foreground">{td(t, 'predictions.rankAutocorrHelp')}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgTopNRetention')}</p>
                  <p className={cn(
                    'text-lg font-semibold',
                    turnover.summary.avgTopNRetention != null && turnover.summary.avgTopNRetention > 0.5 && 'text-green-600 dark:text-green-400',
                  )}>
                    {turnover.summary.avgTopNRetention != null ? formatPercent(turnover.summary.avgTopNRetention) : '-'}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    Top {turnover.summary.topN} {td(t, 'predictions.retentionHelp')}
                  </p>
                </div>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">{td(t, 'predictions.noTurnoverData')}</p>
            )}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

// ── Performance Tracking ──────────────────────

function PerformanceSection() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('cn')
  const [days, setDays] = useState(90)

  const { data: performance, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'performance', selectedMarket, days],
    queryFn: () => predictionsApi.getPerformanceMetrics(selectedMarket, days),
    staleTime: 10 * 60_000,
  })

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Activity className="h-4 w-4" />
              {td(t, 'predictions.performance')}
            </CardTitle>
            <CardDescription className="text-xs">{td(t, 'predictions.performanceDesc')}</CardDescription>
          </div>
          <div className="flex gap-1">
            {[30, 90, 180].map(d => (
              <Button
                key={d}
                variant={days === d ? 'default' : 'outline'}
                size="sm"
                onClick={() => setDays(d)}
              >
                {d}d
              </Button>
            ))}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex gap-1 mb-4">
          {MARKETS.map(mkt => (
            <Button
              key={mkt}
              variant={selectedMarket === mkt ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedMarket(mkt)}
            >
              {mkt.toUpperCase()}
            </Button>
          ))}
        </div>

        {isLoading ? (
          <Skeleton className="h-40 w-full" />
        ) : performance && performance.dataPoints > 0 ? (
          <div className="space-y-4">
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <div>
                <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgIc')}</p>
                <p className={cn(
                  'text-lg font-semibold',
                  performance.summary.avgIc != null && performance.summary.avgIc > 0.03 && 'text-green-600 dark:text-green-400',
                  performance.summary.avgIc != null && performance.summary.avgIc < 0.01 && 'text-red-600 dark:text-red-400',
                )}>
                  {performance.summary.avgIc != null ? performance.summary.avgIc.toFixed(4) : '-'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgHitRate')}</p>
                <p className={cn(
                  'text-lg font-semibold',
                  performance.summary.avgHitRate != null && performance.summary.avgHitRate > 0.55 && 'text-green-600 dark:text-green-400',
                )}>
                  {performance.summary.avgHitRate != null ? `${(performance.summary.avgHitRate * 100).toFixed(1)}%` : '-'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{td(t, 'predictions.avgSpread')}</p>
                <p className={cn(
                  'text-lg font-semibold',
                  performance.summary.avgSpread != null && performance.summary.avgSpread > 0 && 'text-green-600 dark:text-green-400',
                )}>
                  {performance.summary.avgSpread != null ? `${(performance.summary.avgSpread * 100).toFixed(2)}%` : '-'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{td(t, 'predictions.totalDates')}</p>
                <p className="text-lg font-semibold">{performance.summary.totalDates}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">{td(t, 'predictions.totalPredictions')}</p>
                <p className="text-lg font-semibold">{performance.summary.totalPredictions}</p>
              </div>
            </div>

            {/* Simple IC trend table */}
            <div className="overflow-x-auto max-h-64 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-background">
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-1.5 font-medium">{td(t, 'predictions.date')}</th>
                    <th className="text-right py-1.5 font-medium">IC</th>
                    <th className="text-right py-1.5 font-medium">{td(t, 'predictions.hitRate')}</th>
                    <th className="text-right py-1.5 font-medium">Top10</th>
                    <th className="text-right py-1.5 font-medium">Bot10</th>
                    <th className="text-right py-1.5 font-medium">{td(t, 'predictions.spread')}</th>
                    <th className="text-right py-1.5 font-medium">{td(t, 'predictions.symbols')}</th>
                  </tr>
                </thead>
                <tbody>
                  {performance.metrics.map(m => (
                    <tr key={m.date} className="border-b last:border-0 hover:bg-muted/50">
                      <td className="py-1.5">{m.date}</td>
                      <td className={cn(
                        'py-1.5 text-right',
                        m.ic != null && m.ic > 0.03 && 'text-green-600 dark:text-green-400',
                        m.ic != null && m.ic < 0 && 'text-red-600 dark:text-red-400',
                      )}>
                        {m.ic != null ? m.ic.toFixed(4) : '-'}
                      </td>
                      <td className={cn(
                        'py-1.5 text-right',
                        m.hitRate != null && m.hitRate > 0.55 && 'text-green-600 dark:text-green-400',
                        m.hitRate != null && m.hitRate < 0.45 && 'text-red-600 dark:text-red-400',
                      )}>
                        {m.hitRate != null ? `${(m.hitRate * 100).toFixed(1)}%` : '-'}
                      </td>
                      <td className={cn(
                        'py-1.5 text-right',
                        m.top10Return > 0 && 'text-green-600 dark:text-green-400',
                        m.top10Return < 0 && 'text-red-600 dark:text-red-400',
                      )}>
                        {(m.top10Return * 100).toFixed(2)}%
                      </td>
                      <td className={cn(
                        'py-1.5 text-right',
                        m.bottom10Return > 0 && 'text-green-600 dark:text-green-400',
                        m.bottom10Return < 0 && 'text-red-600 dark:text-red-400',
                      )}>
                        {(m.bottom10Return * 100).toFixed(2)}%
                      </td>
                      <td className={cn(
                        'py-1.5 text-right',
                        m.spread > 0 && 'text-green-600 dark:text-green-400',
                        m.spread < 0 && 'text-red-600 dark:text-red-400',
                      )}>
                        {(m.spread * 100).toFixed(2)}%
                      </td>
                      <td className="py-1.5 text-right">{m.symbolCount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="text-center py-4 space-y-1">
            <p className="text-sm text-muted-foreground">{td(t, 'predictions.noPerformanceData')}</p>
            <p className="text-xs text-muted-foreground">{td(t, 'predictions.needsReturnBackfill')}</p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// ── Holdings Change ────────────────────────────

function HoldingsChangeTable() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('us')

  const { data, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'prediction-dates', selectedMarket],
    queryFn: () => predictionsApi.getPredictionDates(selectedMarket, 2),
    staleTime: 5 * 60_000,
  })

  const changes = useMemo(() => {
    if (!data || data.dates.length < 2) return null
    const today = data.dates[0]
    const yesterday = data.dates[1]
    if (!today || !yesterday) return null
    const todayPreds = data.predictions[today] ?? []
    const yesterdayPreds = data.predictions[yesterday] ?? []
    const todayTop20 = todayPreds.slice(0, 20)
    const yesterdayTop20 = yesterdayPreds.slice(0, 20)
    const todaySyms = new Set(todayTop20.map((p: PredictionResult) => p.symbol))
    const yestSyms = new Set(yesterdayTop20.map((p: PredictionResult) => p.symbol))

    const entered = todayTop20.filter((p: PredictionResult) => !yestSyms.has(p.symbol))
    const exited = yesterdayTop20.filter((p: PredictionResult) => !todaySyms.has(p.symbol))
    const retained = todayTop20.filter((p: PredictionResult) => yestSyms.has(p.symbol))

    return { entered, exited, retained, todayDate: today, yesterdayDate: yesterday }
  }, [data])

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-medium">{td(t, 'predictions.holdingsChange')}</p>
        <div className="flex gap-1">
          {MARKETS.map(mkt => (
            <Button key={mkt} variant={selectedMarket === mkt ? 'default' : 'outline'} size="sm"
              className="h-6 text-xs px-2" onClick={() => setSelectedMarket(mkt)}>
              {mkt.toUpperCase()}
            </Button>
          ))}
        </div>
      </div>
      {isLoading ? <Skeleton className="h-24 w-full" /> : !changes ? (
        <p className="text-xs text-muted-foreground">{td(t, 'predictions.noHistoryData')}</p>
      ) : (
        <div className="grid grid-cols-3 gap-3">
          {/* New Entries */}
          <div>
            <p className="text-xs font-medium text-green-600 dark:text-green-400 mb-1 flex items-center gap-1">
              <ArrowUpRight className="h-3 w-3" /> {td(t, 'predictions.newEntries')} ({changes.entered.length})
            </p>
            <div className="space-y-0.5">
              {changes.entered.map((p: PredictionResult) => (
                <div key={p.symbol} className="text-xs flex justify-between">
                  <span className="font-mono">{p.symbol}</span>
                  <span className="text-muted-foreground">{formatPercent(p.percentileRank)}</span>
                </div>
              ))}
            </div>
          </div>
          {/* Retained */}
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1 flex items-center gap-1">
              <Minus className="h-3 w-3" /> {td(t, 'predictions.retained')} ({changes.retained.length})
            </p>
            <div className="space-y-0.5">
              {changes.retained.map((p: PredictionResult) => (
                <div key={p.symbol} className="text-xs flex justify-between">
                  <span className="font-mono">{p.symbol}</span>
                  <span className="text-muted-foreground">{formatPercent(p.percentileRank)}</span>
                </div>
              ))}
            </div>
          </div>
          {/* Exits */}
          <div>
            <p className="text-xs font-medium text-red-600 dark:text-red-400 mb-1 flex items-center gap-1">
              <ArrowDownRight className="h-3 w-3" /> {td(t, 'predictions.exits')} ({changes.exited.length})
            </p>
            <div className="space-y-0.5">
              {changes.exited.map((p: PredictionResult) => (
                <div key={p.symbol} className="text-xs flex justify-between">
                  <span className="font-mono">{p.symbol}</span>
                  <span className="text-muted-foreground">{formatPercent(p.percentileRank)}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Sector Allocation ─────────────────────────

function SectorAllocationCard() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('us')

  const { data: sectors } = useQuery({
    queryKey: ['admin', 'predictions', 'sectors', selectedMarket],
    queryFn: () => predictionsApi.getSectors(selectedMarket),
    staleTime: 30 * 60_000,
  })

  const { data: predData } = useQuery({
    queryKey: ['admin', 'predictions', 'latest', selectedMarket],
    queryFn: () => predictionsApi.getLatestPredictions(selectedMarket, 100),
    staleTime: 5 * 60_000,
  })

  const allocation = useMemo(() => {
    if (!sectors || !predData || !sectors.sectorCounts) return null
    const predList = Array.isArray(predData) ? predData : []
    if (predList.length === 0) return null

    // Universe sector weights
    const univTotal = sectors.totalSymbols || 1
    const univWeights: Record<string, number> = {}
    for (const [s, cnt] of Object.entries(sectors.sectorCounts)) {
      univWeights[s] = cnt / univTotal
    }

    // Portfolio: top 20 — we need sector data per symbol
    // Since we don't have per-symbol sector from this endpoint,
    // show universe distribution only
    return {
      sectors: Object.entries(sectors.sectorCounts)
        .sort(([, a], [, b]) => b - a)
        .slice(0, 10)
        .map(([name, count]) => ({
          name,
          count,
          pct: count / univTotal * 100,
        })),
      total: sectors.totalSymbols,
      unique: sectors.uniqueSectors,
    }
  }, [sectors, predData])

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-medium">{td(t, 'predictions.sectorAllocation')}</p>
        <div className="flex gap-1">
          {MARKETS.map(mkt => (
            <Button key={mkt} variant={selectedMarket === mkt ? 'default' : 'outline'} size="sm"
              className="h-6 text-xs px-2" onClick={() => setSelectedMarket(mkt)}>
              {mkt.toUpperCase()}
            </Button>
          ))}
        </div>
      </div>
      {!allocation ? (
        <p className="text-xs text-muted-foreground">{td(t, 'predictions.noSectorData')}</p>
      ) : (
        <div className="space-y-1.5">
          {allocation.sectors.map(s => (
            <div key={s.name} className="flex items-center gap-2 text-xs">
              <span className="w-28 truncate">{s.name}</span>
              <div className="flex-1 bg-muted rounded-full h-3 overflow-hidden">
                <div
                  className="bg-primary/60 h-full rounded-full transition-all"
                  style={{ width: `${Math.min(s.pct * 2, 100)}%` }}
                />
              </div>
              <span className="w-12 text-right text-muted-foreground">{s.pct.toFixed(1)}%</span>
              <span className="w-8 text-right text-muted-foreground">{s.count}</span>
            </div>
          ))}
          <p className="text-[10px] text-muted-foreground mt-1">
            {allocation.unique} {td(t, 'predictions.sectorCount')} · {allocation.total} {td(t, 'predictions.symbol')}s
          </p>
        </div>
      )}
    </div>
  )
}

// ── Return Attribution ────────────────────────

function AttributionSummaryCard() {
  const { t } = useTranslation('admin')
  const [selectedMarket, setSelectedMarket] = useState<MarketKey>('us')

  const { data: attribution, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'attribution', selectedMarket],
    queryFn: () => predictionsApi.getAttribution(selectedMarket),
    staleTime: 10 * 60_000,
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm font-medium">{td(t, 'predictions.attribution')}</p>
        <div className="flex gap-1">
          {MARKETS.map(mkt => (
            <Button key={mkt} variant={selectedMarket === mkt ? 'default' : 'outline'} size="sm"
              className="h-6 text-xs px-2" onClick={() => setSelectedMarket(mkt)}>
              {mkt.toUpperCase()}
            </Button>
          ))}
        </div>
      </div>
      {isLoading ? <Skeleton className="h-24 w-full" /> : !attribution || attribution.dataPoints === 0 ? (
        <p className="text-xs text-muted-foreground">{td(t, 'predictions.noAttributionData')}</p>
      ) : (
        <div className="space-y-3">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg border p-2.5 text-center">
              <p className="text-[10px] text-muted-foreground">{td(t, 'predictions.sectorReturn')}</p>
              <p className={cn('text-lg font-bold',
                attribution.summary.sectorPct > 40 && 'text-amber-600 dark:text-amber-400'
              )}>
                {attribution.summary.sectorPct.toFixed(1)}%
              </p>
            </div>
            <div className="rounded-lg border p-2.5 text-center">
              <p className="text-[10px] text-muted-foreground">{td(t, 'predictions.sizeReturn')}</p>
              <p className="text-lg font-bold">
                {attribution.summary.sizePct.toFixed(1)}%
              </p>
            </div>
            <div className="rounded-lg border p-2.5 text-center">
              <p className="text-[10px] text-muted-foreground">{td(t, 'predictions.alphaReturn')}</p>
              <p className={cn('text-lg font-bold',
                attribution.summary.alphaPct > 50 && 'text-green-600 dark:text-green-400',
                attribution.summary.alphaPct < 20 && 'text-red-600 dark:text-red-400',
              )}>
                {attribution.summary.alphaPct.toFixed(1)}%
              </p>
            </div>
          </div>
          {/* Sector breakdown */}
          {Object.keys(attribution.summary.sectorBreakdown).length > 0 && (
            <div>
              <p className="text-xs text-muted-foreground mb-1">{td(t, 'predictions.sectorBreakdown')}</p>
              <div className="space-y-1">
                {Object.entries(attribution.summary.sectorBreakdown).slice(0, 6).map(([sector, contrib]) => (
                  <div key={sector} className="flex items-center gap-2 text-xs">
                    <span className="w-28 truncate">{sector}</span>
                    <div className="flex-1 bg-muted rounded-full h-2.5 overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full transition-all',
                          contrib >= 0 ? 'bg-green-500/60' : 'bg-red-500/60',
                        )}
                        style={{ width: `${Math.min(Math.abs(contrib) * 5000, 100)}%` }}
                      />
                    </div>
                    <span className={cn('w-16 text-right',
                      contrib >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400',
                    )}>
                      {(contrib * 100).toFixed(2)}%
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
          <p className="text-[10px] text-muted-foreground">
            {td(t, 'predictions.attributionPeriod', { days: attribution.days, count: attribution.dataPoints })}
          </p>
        </div>
      )}
    </div>
  )
}

// ── Signal Dashboard (container) ──────────────

function SignalDashboardSection() {
  const { t } = useTranslation('admin')
  const [expanded, setExpanded] = useState(false)

  return (
    <Card>
      <CardHeader className="pb-2 cursor-pointer" onClick={() => setExpanded(prev => !prev)}>
        <CardTitle className="flex items-center gap-2 text-base">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <PieChart className="h-4 w-4" />
          {td(t, 'predictions.signalDashboard')}
        </CardTitle>
        <CardDescription className="text-xs">{td(t, 'predictions.signalDashboardDesc')}</CardDescription>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-6">
          <HoldingsChangeTable />
          <div className="border-t pt-4">
            <SectorAllocationCard />
          </div>
          <div className="border-t pt-4">
            <AttributionSummaryCard />
          </div>
        </CardContent>
      )}
    </Card>
  )
}

// ── Main Component ─────────────────────────────

export default function Predictions() {
  const { t } = useTranslation('admin')
  const [showRDAgent, setShowRDAgent] = useState(false)

  const { data: status, isLoading: statusLoading, error: statusError } = useQuery({
    queryKey: ['admin', 'predictions', 'status'],
    queryFn: predictionsApi.getStatus,
    staleTime: 60_000,
  })

  if (statusError) {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-8 justify-center text-muted-foreground">
          <AlertCircle className="h-5 w-5" />
          <span>{td(t, 'predictions.loadError')}</span>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      {/* Status Overview */}
      {statusLoading ? (
        <div className="grid gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : status ? (
        <StatusOverview status={status} />
      ) : null}

      {/* Manual Trigger */}
      <TriggerSection />

      {/* Latest Predictions */}
      <PredictionResultsSection />

      {/* Accuracy */}
      <AccuracySection />

      {/* Performance Tracking */}
      <PerformanceSection />

      {/* Signal Quality (IC Decay, Turnover, Sectors) — collapsible */}
      <SignalQualitySection />

      {/* Signal Dashboard (Holdings Change, Sector Allocation, Attribution) — collapsible */}
      <SignalDashboardSection />

      {/* Model History (collapsible) */}
      <ModelHistorySection />

      {/* Backtest */}
      <BacktestPanel />

      {/* RD-Agent Section (collapsible) */}
      <Card>
        <CardHeader
          className="pb-2 cursor-pointer"
          onClick={() => setShowRDAgent(prev => !prev)}
        >
          <CardTitle className="flex items-center gap-2 text-base">
            {showRDAgent ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            <Brain className="h-4 w-4" />
            {td(t, 'predictions.rdAgent')}
          </CardTitle>
          <CardDescription className="text-xs">{td(t, 'predictions.rdAgentDesc')}</CardDescription>
        </CardHeader>
        {showRDAgent && (
          <CardContent>
            <RDAgent />
          </CardContent>
        )}
      </Card>
    </div>
  )
}
