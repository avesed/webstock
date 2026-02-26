import { useState, useCallback, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  TrendingUp, Play, RefreshCw, Loader2, ChevronDown, ChevronRight,
  BarChart3, Target, Brain, AlertCircle,
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
                    <span>{latestPrediction?.predictionDate ?? '-'}</span>
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
  const [expanded, setExpanded] = useState(false)

  const { data: models, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'models'],
    queryFn: () => predictionsApi.getModels(),
    staleTime: 5 * 60_000,
  })

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
                    <th className="text-right py-2 font-medium">{td(t, 'predictions.features')}</th>
                    <th className="text-right py-2 font-medium">{td(t, 'predictions.symbols')}</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m: PredictionModel) => (
                    <tr key={m.id} className="border-b last:border-0 hover:bg-muted/50">
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
                      <td className="py-2 text-right">{m.featureCount}</td>
                      <td className="py-2 text-right">{m.symbolCount}</td>
                    </tr>
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
        ) : accuracy ? (
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
          <p className="text-sm text-muted-foreground text-center py-4">{td(t, 'predictions.noAccuracyData')}</p>
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

      {/* Model History (collapsible) */}
      <ModelHistorySection />

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
