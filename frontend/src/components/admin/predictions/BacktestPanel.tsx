import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Play, Loader2, Trash2,
  Brain, Clock, BarChart3, CheckCircle2, XCircle,
  RefreshCw,
} from 'lucide-react'

import { predictionsApi } from '@/api/predictions'
import type {
  BacktestConfig,
  BacktestIteration, BacktestPhaseResult,
} from '@/api/predictions'
import { getErrorMessage } from '@/api/client'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { useToast } from '@/hooks/useToast'
import { cn } from '@/lib/utils'

const MARKETS = ['us', 'cn', 'hk'] as const

function formatDuration(seconds: number | null): string {
  if (!seconds) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const min = Math.floor(seconds / 60)
  const sec = Math.round(seconds % 60)
  return `${min}m ${sec}s`
}

function formatIc(v: number | null): string {
  if (v === null || v === undefined) return '-'
  return v.toFixed(4)
}

function formatPercent(v: number | null): string {
  if (v === null || v === undefined) return '-'
  return `${(v * 100).toFixed(1)}%`
}

export default function BacktestPanel() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const qc = useQueryClient()

  // Form state
  const [market, setMarket] = useState<string>('us')
  const [cutoffDate, setCutoffDate] = useState(() => {
    const d = new Date()
    d.setDate(d.getDate() - 60)
    return d.toISOString().split('T')[0]!
  })
  const [validationDays, setValidationDays] = useState(60)
  const [forwardDays, setForwardDays] = useState(5)
  const [useLlmAgents, setUseLlmAgents] = useState(false)
  const [maxIterations, setMaxIterations] = useState(3)
  const [configOverride, setConfigOverride] = useState('')

  // Active task
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null)

  // Expanded history rows
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // History query
  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['backtests', market],
    queryFn: () => predictionsApi.listBacktests(market),
    staleTime: 30_000,
  })

  // Task polling
  const { data: taskStatus } = useQuery({
    queryKey: ['backtest-task', activeTaskId],
    queryFn: () => activeTaskId ? predictionsApi.getBacktestTaskStatus(activeTaskId) : null,
    enabled: !!activeTaskId,
    refetchInterval: activeTaskId ? 2000 : false,
  })

  // Auto-clear task when completed/failed
  useEffect(() => {
    if (taskStatus && (taskStatus.status === 'completed' || taskStatus.status === 'failed')) {
      qc.invalidateQueries({ queryKey: ['backtests', market] })
      if (taskStatus.status === 'completed') {
        toast({ title: t('predictions.backtest.completed'), description: taskStatus.message })
      }
    }
  }, [taskStatus?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // Start mutation
  const startMutation = useMutation({
    mutationFn: (config: BacktestConfig) => predictionsApi.startBacktest(market, config),
    onSuccess: (data) => {
      setActiveTaskId(data.taskId)
      toast({ title: t('predictions.backtest.started') })
    },
    onError: (err) => {
      toast({ title: t('predictions.backtest.startError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  // Delete mutation
  const deleteMutation = useMutation({
    mutationFn: (id: string) => predictionsApi.deleteBacktest(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['backtests', market] })
      toast({ title: t('predictions.backtest.deleted') })
    },
    onError: (err) => {
      toast({ title: t('predictions.backtest.deleteError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const handleStart = useCallback(() => {
    let parsedOverride: Record<string, unknown> | null = null
    if (configOverride.trim()) {
      try {
        parsedOverride = JSON.parse(configOverride) as Record<string, unknown>
      } catch {
        toast({ title: t('predictions.backtest.invalidJson'), variant: 'destructive' })
        return
      }
    }
    startMutation.mutate({
      cutoffDate,
      validationDays,
      forwardDays,
      configOverride: parsedOverride,
      useLlmAgents,
      maxIterations: useLlmAgents ? maxIterations : 1,
    })
  }, [market, cutoffDate, validationDays, forwardDays, useLlmAgents, maxIterations, configOverride, startMutation, toast, t]) // eslint-disable-line react-hooks/exhaustive-deps

  const isRunning = !!activeTaskId && taskStatus && taskStatus.status === 'running'

  return (
    <div className="space-y-6">
      {/* New Backtest Form */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('predictions.backtest.title')}
          </CardTitle>
          <CardDescription>{t('predictions.backtest.desc')}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Market selector */}
          <div className="flex gap-2">
            {MARKETS.map(m => (
              <Button
                key={m}
                size="sm"
                variant={market === m ? 'default' : 'outline'}
                onClick={() => setMarket(m)}
              >
                {m.toUpperCase()}
              </Button>
            ))}
          </div>

          {/* Config fields */}
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.cutoffDate')}</label>
              <input
                type="date"
                value={cutoffDate}
                onChange={e => setCutoffDate(e.target.value)}
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm bg-background"
              />
            </div>
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.validationDays')}</label>
              <input
                type="number"
                min={10}
                max={250}
                value={validationDays}
                onChange={e => setValidationDays(Number(e.target.value))}
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm bg-background"
              />
            </div>
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.forwardDays')}</label>
              <input
                type="number"
                min={1}
                max={30}
                value={forwardDays}
                onChange={e => setForwardDays(Number(e.target.value))}
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm bg-background"
              />
            </div>
          </div>

          {/* LLM agents toggle */}
          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={useLlmAgents}
                onChange={e => setUseLlmAgents(e.target.checked)}
                className="rounded"
              />
              <Brain className="h-4 w-4" />
              {t('predictions.backtest.enableLlm')}
            </label>
            {useLlmAgents && (
              <div className="flex items-center gap-2">
                <label className="text-sm">{t('predictions.backtest.maxIterations')}</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={maxIterations}
                  onChange={e => setMaxIterations(Number(e.target.value))}
                  className="w-16 rounded-md border px-2 py-1 text-sm bg-background"
                />
              </div>
            )}
          </div>

          {/* Config override */}
          <div>
            <label className="text-sm font-medium">{t('predictions.backtest.configOverride')}</label>
            <textarea
              value={configOverride}
              onChange={e => setConfigOverride(e.target.value)}
              placeholder='{"nan_threshold": 0.80, "num_boost_round": 800}'
              rows={2}
              className="mt-1 w-full rounded-md border px-3 py-2 text-xs font-mono bg-background"
            />
          </div>

          <Button
            onClick={handleStart}
            disabled={startMutation.isPending || !!isRunning}
          >
            {startMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Play className="mr-2 h-4 w-4" />
            )}
            {t('predictions.backtest.startBtn')}
          </Button>
        </CardContent>
      </Card>

      {/* Running Task Progress */}
      {taskStatus && taskStatus.status !== 'completed' && taskStatus.status !== 'failed' && activeTaskId && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('predictions.backtest.running')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={taskStatus.progress} className="h-2" />
            <div className="flex justify-between text-sm text-muted-foreground">
              <span>
                {taskStatus.maxIterations > 1
                  ? `${t('predictions.backtest.iteration')} ${taskStatus.currentIteration}/${taskStatus.maxIterations} — `
                  : ''}
                {taskStatus.message}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="h-3 w-3" />
                {formatDuration(taskStatus.elapsedSeconds)}
              </span>
            </div>

            {/* Completed iterations */}
            {taskStatus.iterations.map(iter => (
              <IterationCard key={iter.iteration} iteration={iter} />
            ))}
          </CardContent>
        </Card>
      )}

      {/* Task Completed/Failed Banner */}
      {taskStatus && (taskStatus.status === 'completed' || taskStatus.status === 'failed') && activeTaskId && (
        <Card className={cn(
          'border-l-4',
          taskStatus.status === 'completed' ? 'border-l-green-500' : 'border-l-red-500'
        )}>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {taskStatus.status === 'completed' ? (
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                ) : (
                  <XCircle className="h-5 w-5 text-red-500" />
                )}
                <span className="font-medium">{taskStatus.message || taskStatus.status}</span>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setActiveTaskId(null)}>
                {t('predictions.backtest.dismiss')}
              </Button>
            </div>
            {/* Show last iteration results */}
            {taskStatus.iterations.map(iter => (
              <IterationCard key={iter.iteration} iteration={iter} />
            ))}
          </CardContent>
        </Card>
      )}

      {/* History */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('predictions.backtest.history')}</CardTitle>
          <CardDescription>{t('predictions.backtest.historyDesc')}</CardDescription>
        </CardHeader>
        <CardContent>
          {historyLoading ? (
            <div className="space-y-2">
              {[1, 2, 3].map(i => <div key={i} className="h-10 bg-muted rounded animate-pulse" />)}
            </div>
          ) : !historyData?.backtests?.length ? (
            <p className="text-sm text-muted-foreground">{t('predictions.backtest.noHistory')}</p>
          ) : (
            <div className="space-y-1">
              {/* Header */}
              <div className="grid grid-cols-8 gap-2 text-xs font-medium text-muted-foreground px-2 py-1">
                <span>{t('predictions.backtest.cutoffDate')}</span>
                <span>{t('predictions.marketLabel')}</span>
                <span>{t('predictions.backtest.valIc')}</span>
                <span>{t('predictions.backtest.valIcir')}</span>
                <span>{t('predictions.spread')}</span>
                <span>{t('predictions.directionAccuracy')}</span>
                <span>{t('predictions.backtest.duration')}</span>
                <span></span>
              </div>
              {historyData.backtests.map(bt => (
                <div key={bt.id}>
                  <div
                    className="grid grid-cols-8 gap-2 text-sm px-2 py-2 hover:bg-muted/50 rounded cursor-pointer items-center"
                    onClick={() => setExpandedId(expandedId === bt.id ? null : bt.id)}
                  >
                    <span className="font-mono text-xs">{bt.cutoffDate}</span>
                    <span>{bt.market.toUpperCase()}</span>
                    <span className={cn(bt.valIc !== null && bt.valIc > 0 ? 'text-green-600' : 'text-red-600')}>
                      {formatIc(bt.valIc)}
                    </span>
                    <span>{formatIc(bt.valIcir)}</span>
                    <span>{bt.valSpread !== null ? `${(bt.valSpread * 100).toFixed(2)}%` : '-'}</span>
                    <span>{formatPercent(bt.valDirectionAccuracy)}</span>
                    <span className="text-xs text-muted-foreground">{formatDuration(bt.durationSeconds)}</span>
                    <div className="flex items-center gap-1">
                      <StatusBadge status={bt.status} />
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 w-6 p-0"
                        onClick={e => {
                          e.stopPropagation()
                          if (window.confirm(t('predictions.backtest.deleteConfirm'))) {
                            deleteMutation.mutate(bt.id)
                          }
                        }}
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </div>
                  </div>
                  {expandedId === bt.id && <BacktestDetailView backtestId={bt.id} />}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

// ── Sub-components ──────────────────────────────

function StatusBadge({ status }: { status: string }) {
  const cls = {
    completed: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
    failed: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
    running: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
    pending: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200',
  }[status] ?? 'bg-gray-100 text-gray-800'

  return <Badge className={cn('text-xs', cls)}>{status}</Badge>
}

function IterationCard({ iteration }: { iteration: BacktestIteration }) {
  const { t } = useTranslation('admin')
  const training = iteration.phases.training as BacktestPhaseResult | undefined
  const evaluator = iteration.phases.evaluator as BacktestPhaseResult | undefined
  const strategist = iteration.phases.strategist as BacktestPhaseResult | undefined

  return (
    <div className="rounded border p-3 mt-2 text-sm space-y-2 bg-muted/30">
      <div className="flex items-center justify-between">
        <span className="font-medium">
          {t('predictions.backtest.iterationLabel', { n: iteration.iteration })}
        </span>
        <span className="text-xs text-muted-foreground">{formatDuration(iteration.durationSeconds)}</span>
      </div>

      {/* Training metrics */}
      {training && training.status === 'completed' && (
        <div className="flex gap-4 text-xs">
          <span>IC: <strong>{training.meanIc?.toFixed(4) ?? '-'}</strong></span>
          <span>{t('predictions.features')}: {training.featureCount ?? '-'}</span>
          {training.foldIcs && (
            <span>{t('predictions.foldIcs')}: [{training.foldIcs.map(ic => ic.toFixed(4)).join(', ')}]</span>
          )}
        </div>
      )}

      {/* Strategist reasoning */}
      {strategist?.reasoning && (
        <div className="text-xs text-muted-foreground">
          <Brain className="inline h-3 w-3 mr-1" />
          {strategist.reasoning}
          {strategist.configChanges?.length ? (
            <span className="ml-2 font-mono">[{strategist.configChanges.join(', ')}]</span>
          ) : null}
        </div>
      )}

      {/* Evaluator decision */}
      {evaluator && (
        <div className="flex items-center gap-2 text-xs">
          {evaluator.decision === 'deploy' && <CheckCircle2 className="h-3 w-3 text-green-500" />}
          {evaluator.decision === 'retry' && <RefreshCw className="h-3 w-3 text-yellow-500" />}
          {evaluator.decision === 'reject' && <XCircle className="h-3 w-3 text-red-500" />}
          <span className="font-medium">{
            evaluator.decision === 'deploy' ? t('predictions.backtest.deploy') :
            evaluator.decision === 'retry' ? t('predictions.backtest.retry') :
            evaluator.decision === 'reject' ? t('predictions.backtest.reject') :
            evaluator.decision
          }</span>
          {evaluator.confidence !== undefined && (
            <span className="text-muted-foreground">({(evaluator.confidence * 100).toFixed(0)}%)</span>
          )}
          {evaluator.valIc !== undefined && <span>val_IC={evaluator.valIc.toFixed(4)}</span>}
          {evaluator.reasoning && (
            <span className="text-muted-foreground truncate max-w-md">{evaluator.reasoning}</span>
          )}
        </div>
      )}
    </div>
  )
}

function BacktestDetailView({ backtestId }: { backtestId: string }) {
  const { t } = useTranslation('admin')
  const { data, isLoading } = useQuery({
    queryKey: ['backtest-detail', backtestId],
    queryFn: () => predictionsApi.getBacktestDetail(backtestId),
    staleTime: 60_000,
  })

  if (isLoading) return <div className="p-4"><Loader2 className="h-4 w-4 animate-spin" /></div>
  if (!data) return null

  return (
    <div className="p-3 bg-muted/20 rounded-b space-y-3 text-sm">
      <div className="grid gap-3 sm:grid-cols-4">
        <MetricCard label={t('predictions.backtest.trainIc')} value={formatIc(data.trainIc)} />
        <MetricCard label={t('predictions.backtest.trainIcir')} value={formatIc(data.trainIcir)} />
        <MetricCard label={t('predictions.backtest.valIc')} value={formatIc(data.valIc)} />
        <MetricCard label={t('predictions.backtest.valIcir')} value={formatIc(data.valIcir)} />
        <MetricCard label={t('predictions.directionAccuracy')} value={formatPercent(data.valDirectionAccuracy)} />
        <MetricCard label={t('predictions.spread')} value={data.valSpread !== null ? `${(data.valSpread * 100).toFixed(2)}%` : '-'} />
        <MetricCard label={t('predictions.hitRate')} value={formatPercent(data.valHitRate)} />
        <MetricCard label={t('predictions.backtest.maxDrawdown')} value={data.valMaxDrawdown !== null ? `${(data.valMaxDrawdown * 100).toFixed(1)}%` : '-'} />
      </div>
      <div className="text-xs text-muted-foreground space-y-1">
        <div>{t('predictions.features')}: {data.featureCount} · {t('predictions.symbols')}: {data.symbolCount} · {t('predictions.ensemble')}: {data.ensembleSize}</div>
        {data.foldIcs && <div>{t('predictions.foldIcs')}: [{data.foldIcs.map((ic: number) => ic.toFixed(4)).join(', ')}]</div>}
        {data.error && <div className="text-red-500">{data.error}</div>}
      </div>
    </div>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border p-2 text-center">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-mono font-medium">{value}</div>
    </div>
  )
}
