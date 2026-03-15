import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Play, Loader2, Trash2,
  Brain, Clock, BarChart3, CheckCircle2, XCircle,
  RefreshCw, AlertTriangle,
} from 'lucide-react'

import { useAdminStore } from '@/stores/adminStore'
import { predictionsApi } from '@/api/predictions'
import type {
  BacktestConfig,
  BacktestIteration, BacktestPhaseResult, RollingResults,
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
  if (seconds === null || seconds === undefined) return '-'
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
  const [validationDays, setValidationDays] = useState(10)
  const [forwardDays, setForwardDays] = useState(5)
  const [backtestType, setBacktestType] = useState<'static' | 'rolling'>('static')
  const [retrainInterval, setRetrainInterval] = useState(5)
  const [useLlmAgents, setUseLlmAgents] = useState(false)
  const [maxIterations, setMaxIterations] = useState(3)
  const [configOverride, setConfigOverride] = useState('')

  // Active task — persisted in Zustand store (survives page refresh)
  const {
    activeBacktestTaskId: activeTaskId,
    activeAgentTaskId: agentBacktestId,
    setActiveBacktestTask,
    setActiveAgentTask,
  } = useAdminStore()

  // Expanded history rows
  const [expandedId, setExpandedId] = useState<string | null>(null)

  // History query
  const { data: historyData, isLoading: historyLoading } = useQuery({
    queryKey: ['backtests', market],
    queryFn: () => predictionsApi.listBacktests(market),
    staleTime: 30_000,
  })

  // Task polling — stops on terminal status
  const { data: taskStatus } = useQuery({
    queryKey: ['backtest-task', activeTaskId],
    queryFn: () => activeTaskId ? predictionsApi.getBacktestTaskStatus(activeTaskId) : null,
    enabled: !!activeTaskId,
    refetchInterval: (query) => {
      const s = query.state.data
      if (s && (s.status === 'completed' || s.status === 'failed')) return false
      return activeTaskId ? 2000 : false
    },
    refetchIntervalInBackground: false,
  })

  // Agent task polling — stops on terminal status
  const { data: agentStatus } = useQuery({
    queryKey: ['agent-task', agentBacktestId],
    queryFn: () => agentBacktestId ? predictionsApi.getAgentTaskStatus(agentBacktestId) : null,
    enabled: !!agentBacktestId,
    refetchInterval: (query) => {
      const s = query.state.data
      if (s && (s.status === 'completed' || s.status === 'failed')) return false
      return agentBacktestId ? 3000 : false
    },
    refetchIntervalInBackground: false,
  })

  // Sub-task polling — when agent is suspended, poll the underlying ml-tools task
  const pendingTaskId = agentStatus?.pendingTaskId ?? null
  const isSuspended = agentStatus?.status === 'suspended'
  const { data: pendingTaskStatus } = useQuery({
    queryKey: ['ml-tools-subtask', pendingTaskId],
    queryFn: () => pendingTaskId ? predictionsApi.getMlToolsTaskStatus(pendingTaskId) : null,
    enabled: !!pendingTaskId && isSuspended,
    refetchInterval: (query) => {
      const s = query.state.data
      if (s && (s.status === 'completed' || s.status === 'failed')) return false
      return (pendingTaskId && isSuspended) ? 2000 : false
    },
    refetchIntervalInBackground: false,
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

  // Auto-clear agent task on completion
  useEffect(() => {
    if (agentStatus && (agentStatus.status === 'completed' || agentStatus.status === 'failed')) {
      qc.invalidateQueries({ queryKey: ['backtests', market] })
      if (agentStatus.status === 'completed') {
        toast({ title: t('predictions.backtest.completed') })
      }
    }
  }, [agentStatus?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  // Start mutation
  const startMutation = useMutation({
    mutationFn: (config: BacktestConfig) => predictionsApi.startBacktest(market, config),
    onSuccess: (data) => {
      setActiveBacktestTask(data.taskId, market)
      toast({ title: t('predictions.backtest.started') })
    },
    onError: (err) => {
      toast({ title: t('predictions.backtest.startError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  // Agent-mode start mutation
  const agentStartMutation = useMutation({
    mutationFn: (params: { cutoffDate: string; validationDays: number; forwardDays: number; maxIterations: number }) =>
      predictionsApi.startAgentBacktest(market, params),
    onSuccess: (data) => {
      setActiveAgentTask(data.backtestId)
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
    if (useLlmAgents && backtestType === 'static') {
      // Agent mode — use new backend endpoint (no config override, agent decides)
      agentStartMutation.mutate({
        cutoffDate,
        validationDays,
        forwardDays,
        maxIterations,
      })
    } else {
      // Standard or rolling mode — use data-processor endpoint
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
        useLlmAgents: false,
        maxIterations: 1,
        backtestType,
        retrainInterval,
      })
    }
  }, [market, cutoffDate, validationDays, forwardDays, useLlmAgents, maxIterations, configOverride, backtestType, retrainInterval, startMutation, agentStartMutation, toast, t]) // eslint-disable-line react-hooks/exhaustive-deps

  const isRunning = (!!activeTaskId && taskStatus && taskStatus.status === 'running')
    || (!!agentBacktestId && agentStatus && ['pending', 'running', 'suspended'].includes(agentStatus.status))
  const isPending = startMutation.isPending || agentStartMutation.isPending

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

          {/* Mode selector: Static / Rolling / Agent */}
          <div>
            <label className="text-sm font-medium">{t('predictions.backtest.backtestType')}</label>
            <div className="flex gap-2 mt-1">
              <Button
                size="sm"
                variant={backtestType === 'static' && !useLlmAgents ? 'default' : 'outline'}
                onClick={() => { setBacktestType('static'); setUseLlmAgents(false) }}
              >
                {t('predictions.backtest.static')}
              </Button>
              <Button
                size="sm"
                variant={backtestType === 'rolling' && !useLlmAgents ? 'default' : 'outline'}
                onClick={() => { setBacktestType('rolling'); setUseLlmAgents(false) }}
              >
                <RefreshCw className="mr-1 h-3 w-3" />
                {t('predictions.backtest.rolling')}
              </Button>
              <Button
                size="sm"
                variant={useLlmAgents ? 'default' : 'outline'}
                onClick={() => { setUseLlmAgents(true); setBacktestType('static') }}
              >
                <Brain className="mr-1 h-3 w-3" />
                {t('predictions.backtest.agentMode')}
              </Button>
            </div>
          </div>

          {/* Common config fields */}
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.cutoffDate')}</label>
              <input
                type="date"
                value={cutoffDate}
                onChange={e => setCutoffDate(e.target.value)}
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm bg-background"
              />
              <span className="text-xs text-muted-foreground">{t('predictions.backtest.cutoffDateHint')}</span>
            </div>
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.validationDays')}</label>
              <input
                type="number"
                min={5}
                max={250}
                value={validationDays}
                onChange={e => setValidationDays(Number(e.target.value))}
                className="mt-1 w-full rounded-md border px-3 py-2 text-sm bg-background"
              />
              <span className="text-xs text-muted-foreground">{t('predictions.backtest.validationDaysHint')}</span>
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
              <span className="text-xs text-muted-foreground">{t('predictions.backtest.forwardDaysHint')}</span>
            </div>
          </div>

          {/* Rolling mode: retrain interval */}
          {backtestType === 'rolling' && !useLlmAgents && (
            <div className="space-y-1">
              <div className="flex items-center gap-4">
                <div className="flex items-center gap-2">
                  <label className="text-sm font-medium">{t('predictions.backtest.retrainInterval')}</label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={retrainInterval}
                    onChange={e => setRetrainInterval(Number(e.target.value))}
                    className="w-16 rounded-md border px-2 py-1 text-sm bg-background"
                  />
                </div>
                <span className="text-xs text-muted-foreground">
                  ~{Math.ceil(validationDays / retrainInterval)} {t('predictions.backtest.retrainCount')}
                </span>
              </div>
              <span className="text-xs text-muted-foreground">{t('predictions.backtest.retrainIntervalHint')}</span>
            </div>
          )}

          {/* Agent mode: max iterations */}
          {useLlmAgents && (
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <label className="text-sm font-medium">{t('predictions.backtest.maxIterations')}</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={maxIterations}
                  onChange={e => setMaxIterations(Number(e.target.value))}
                  className="w-16 rounded-md border px-2 py-1 text-sm bg-background"
                />
              </div>
              <span className="text-xs text-muted-foreground">{t('predictions.backtest.maxIterationsHint')}</span>
            </div>
          )}

          {/* Config override — shown for static and rolling, hidden for agent */}
          {!useLlmAgents && (
            <div>
              <label className="text-sm font-medium">{t('predictions.backtest.configOverride')}</label>
              <textarea
                value={configOverride}
                onChange={e => setConfigOverride(e.target.value)}
                placeholder='{"num_boost_round": 800, "lgb_overrides": {"learning_rate": 0.01}}'
                rows={2}
                className="mt-1 w-full rounded-md border px-3 py-2 text-xs font-mono bg-background"
              />
            </div>
          )}

          <Button
            onClick={handleStart}
            disabled={isPending || !!isRunning}
          >
            {isPending ? (
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
                {taskStatus.totalRetrains != null && taskStatus.totalRetrains > 0
                  ? `${t('predictions.backtest.retrainProgress', { current: taskStatus.currentRetrain ?? 0, total: taskStatus.totalRetrains })} — `
                  : taskStatus.maxIterations > 1
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
      {taskStatus && (taskStatus.status === 'completed' || taskStatus.status === 'failed') && activeTaskId && (() => {
        const isPass = taskStatus.status === 'completed' && taskStatus.message?.includes('[PASS]')
        const isFail = taskStatus.status === 'failed'
        const borderColor = isFail ? 'border-l-red-500' : isPass ? 'border-l-green-500' : 'border-l-yellow-500'
        const iconColor = isFail ? 'text-red-500' : isPass ? 'text-green-500' : 'text-yellow-500'
        return (
        <Card className={cn('border-l-4', borderColor)}>
          <CardContent className="pt-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isFail ? (
                  <XCircle className={cn('h-5 w-5', iconColor)} />
                ) : isPass ? (
                  <CheckCircle2 className={cn('h-5 w-5', iconColor)} />
                ) : (
                  <AlertTriangle className={cn('h-5 w-5', iconColor)} />
                )}
                <span className="font-medium">{taskStatus.message || taskStatus.status}</span>
              </div>
              <Button size="sm" variant="ghost" onClick={() => setActiveBacktestTask(null)}>
                {t('predictions.backtest.dismiss')}
              </Button>
            </div>
            {/* Show last iteration results */}
            {taskStatus.iterations.map(iter => (
              <IterationCard key={iter.iteration} iteration={iter} />
            ))}
          </CardContent>
        </Card>
        )
      })()}

      {/* Agent Mode Progress */}
      {agentStatus && agentBacktestId && !['completed', 'failed'].includes(agentStatus.status) && (() => {
        const st = agentStatus.status
        const iter = agentStatus.iteration
        const maxIter = agentStatus.maxIterations
        const detail = agentStatus.phaseDetail
        const history = agentStatus.toolHistory ?? []

        // Determine phase label
        let phaseLabel: string
        if (st === 'pending') {
          phaseLabel = t('predictions.backtest.agentPending')
        } else if (st === 'running' && iter === 0) {
          phaseLabel = t('predictions.backtest.agentProfiling')
        } else if (st === 'suspended') {
          phaseLabel = t('predictions.backtest.agentTrainingIter', { iter })
        } else {
          phaseLabel = t('predictions.backtest.agentAnalyzing', { iter })
        }

        // Progress: pending=5%, profiling=10%, then each iteration splits rest
        // When suspended, use sub-task progress for smooth updates
        const subProg = pendingTaskStatus?.progress as number | undefined
        const subDetail = pendingTaskStatus?.statusDetail as string | undefined
        let progress = 5
        if (st !== 'pending') {
          const iterPct = maxIter > 0 ? 85 / maxIter : 85
          if (st === 'running' && iter === 0) {
            progress = 10
          } else if (st === 'suspended') {
            // Map sub-task 0-100% into this iteration's allocation (50-100% of iterPct)
            const subFrac = (subProg != null && subProg > 0) ? subProg / 100 : 0
            progress = Math.min(95, 10 + (iter - 1) * iterPct + iterPct * (0.3 + 0.65 * subFrac))
          } else {
            progress = Math.min(95, 10 + (iter - 1) * iterPct + iterPct * 0.8)
          }
        }

        return (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              {st === 'suspended' ? (
                <Clock className="h-4 w-4 text-yellow-500" />
              ) : (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              <Brain className="h-4 w-4" />
              {phaseLabel}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Progress bar */}
            <div className="space-y-1.5">
              <div className="h-2 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full bg-primary transition-all duration-500"
                  style={{ width: `${progress}%` }}
                />
              </div>
              <div className="flex justify-between text-xs text-muted-foreground">
                <span>{t('predictions.backtest.iteration')} {iter}/{maxIter}</span>
                <span>{Math.round(progress)}%</span>
              </div>
            </div>

            {/* Current phase detail + sub-task progress */}
            {(detail || subDetail) && (
              <div className="text-xs text-muted-foreground bg-muted/50 rounded px-2 py-1.5">
                {detail}
                {subDetail && (
                  <span className="ml-2 text-primary/80">{subDetail}</span>
                )}
              </div>
            )}

            {/* Agent tool call history */}
            {history.length > 0 && (
              <div className="space-y-1">
                <div className="text-xs font-medium text-muted-foreground">{t('predictions.backtest.agentDecisions')}</div>
                <div className="space-y-0.5">
                  {history.map((h, i) => (
                    h.tool === '_reasoning' ? (
                      <div key={i} className="text-xs text-muted-foreground bg-muted/30 rounded px-2 py-1 italic">
                        {h.summary}
                      </div>
                    ) : (
                      <div key={i} className="text-xs text-muted-foreground flex items-start gap-1.5">
                        <span className="text-primary mt-0.5 shrink-0">{'>'}</span>
                        <span>{h.summary}</span>
                      </div>
                    )
                  ))}
                </div>
              </div>
            )}

            {/* IC metrics */}
            {agentStatus.trainIc !== null && (
              <div className="text-xs">
                Train IC: <strong>{agentStatus.trainIc.toFixed(4)}</strong>
                {agentStatus.valIc !== null && (
                  <span className="ml-4">Val IC: <strong>{agentStatus.valIc.toFixed(4)}</strong></span>
                )}
              </div>
            )}
          </CardContent>
        </Card>
        )
      })()}

      {/* Agent Completed/Failed Banner */}
      {agentStatus && agentBacktestId && ['completed', 'failed'].includes(agentStatus.status) && (() => {
        const isFail = agentStatus.status === 'failed'
        const isPass = agentStatus.status === 'completed' && agentStatus.valIc != null && agentStatus.valIc > 0
        const borderColor = isFail ? 'border-l-red-500' : isPass ? 'border-l-green-500' : 'border-l-yellow-500'
        const iconColor = isFail ? 'text-red-500' : isPass ? 'text-green-500' : 'text-yellow-500'
        return (
          <Card className={cn('border-l-4', borderColor)}>
            <CardContent className="pt-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Brain className="h-4 w-4" />
                  {isFail ? (
                    <XCircle className={cn('h-5 w-5', iconColor)} />
                  ) : (
                    <CheckCircle2 className={cn('h-5 w-5', iconColor)} />
                  )}
                  <span className="font-medium">
                    {isFail
                      ? t('predictions.backtest.agentFailed', { error: agentStatus.error || 'Unknown error' })
                      : t('predictions.backtest.agentCompleted', { ic: agentStatus.valIc?.toFixed(4) ?? '-' })}
                  </span>
                </div>
                <Button size="sm" variant="ghost" onClick={() => setActiveAgentTask(null)}>
                  {t('predictions.backtest.dismiss')}
                </Button>
              </div>
            </CardContent>
          </Card>
        )
      })()}

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
                      <StatusBadge status={bt.status} valIc={bt.valIc} />
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

function StatusBadge({ status, valIc }: { status: string; valIc?: number | null }) {
  // For completed backtests, color based on quality (val_IC > 0 = pass)
  const isQualityPass = status === 'completed' && valIc != null && valIc > 0

  const cls = status === 'completed'
    ? (isQualityPass
      ? 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200'
      : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200')
    : ({
      failed: 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
      running: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
      pending: 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200',
    }[status] ?? 'bg-gray-100 text-gray-800')

  const label = status === 'completed' ? (isQualityPass ? 'pass' : 'weak') : status

  return <Badge className={cn('text-xs', cls)}>{label}</Badge>
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

  const results = data.results as Record<string, unknown>
  const isRolling = results.backtest_type === 'rolling'

  return (
    <div className="p-3 bg-muted/20 rounded-b space-y-3 text-sm">
      {/* Metric cards */}
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

      {/* Rolling-specific charts */}
      {isRolling && <RollingResultsView results={results as unknown as RollingResults} />}

      <div className="text-xs text-muted-foreground space-y-1">
        <div>
          {isRolling && <Badge className="text-xs mr-2 bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200">{t('predictions.backtest.rolling')}</Badge>}
          {data.featureCount != null && <>{t('predictions.features')}: {data.featureCount} · </>}
          {data.symbolCount != null && <>{t('predictions.symbols')}: {data.symbolCount} · </>}
          {data.ensembleSize != null && <>{t('predictions.ensemble')}: {data.ensembleSize}</>}
        </div>
        {data.foldIcs && <div>{t('predictions.foldIcs')}: [{data.foldIcs.map((ic: number) => ic.toFixed(4)).join(', ')}]</div>}
        {data.error && <div className="text-red-500">{data.error}</div>}
      </div>
    </div>
  )
}

function RollingResultsView({ results }: { results: RollingResults }) {
  const { t } = useTranslation('admin')

  const icCurve = results.ic_curve ?? []
  const cumReturns = results.cumulative_returns
  const perRetrain = results.per_retrain_metrics ?? []

  return (
    <div className="space-y-4">
      {/* IC Curve */}
      {icCurve.length > 0 && (
        <div>
          <div className="text-xs font-medium mb-2">{t('predictions.backtest.icCurve')}</div>
          <SimpleBarChart
            data={icCurve.map(d => ({ label: d.date.slice(5), value: d.ic }))}
            positiveColor="rgb(34, 197, 94)"
            negativeColor="rgb(239, 68, 68)"
            height={120}
          />
        </div>
      )}

      {/* Cumulative Returns */}
      {cumReturns && (cumReturns.q5?.length > 0 || cumReturns.q1?.length > 0) && (
        <div>
          <div className="text-xs font-medium mb-2">{t('predictions.backtest.cumulativeReturns')}</div>
          <CumulativeReturnChart cumReturns={cumReturns} />
        </div>
      )}

      {/* Per-retrain metrics table */}
      {perRetrain.length > 0 && (
        <div>
          <div className="text-xs font-medium mb-2">{t('predictions.backtest.perRetrainMetrics')}</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-muted-foreground">
                  <th className="text-left py-1 pr-3">{t('predictions.backtest.retrainDate')}</th>
                  <th className="text-right py-1 pr-3">{t('predictions.backtest.trainIc')}</th>
                  <th className="text-right py-1 pr-3">{t('predictions.backtest.windowIc')}</th>
                  <th className="text-right py-1">{t('predictions.backtest.nDates')}</th>
                </tr>
              </thead>
              <tbody>
                {perRetrain.map((m, i) => (
                  <tr key={i} className="border-b border-muted/30">
                    <td className="py-1 pr-3 font-mono">{m.retrain_date}</td>
                    <td className="text-right py-1 pr-3">{m.train_ic != null ? m.train_ic.toFixed(4) : '-'}</td>
                    <td className={cn(
                      'text-right py-1 pr-3 font-medium',
                      m.window_ic != null && m.window_ic > 0 ? 'text-green-600' : 'text-red-600',
                    )}>
                      {m.window_ic != null ? m.window_ic.toFixed(4) : '-'}
                    </td>
                    <td className="text-right py-1">{m.n_dates}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

/** Simple SVG bar chart for IC curve (positive=green, negative=red). */
function SimpleBarChart({
  data,
  positiveColor,
  negativeColor,
  height = 100,
}: {
  data: Array<{ label: string; value: number }>
  positiveColor: string
  negativeColor: string
  height?: number
}) {
  if (data.length === 0) return null

  const maxAbs = Math.max(...data.map(d => Math.abs(d.value)), 0.001)
  const barWidth = Math.max(2, Math.min(12, Math.floor(600 / data.length) - 1))
  const svgWidth = data.length * (barWidth + 1)
  const midY = height / 2

  return (
    <div className="overflow-x-auto rounded border bg-background p-2">
      <svg width={svgWidth} height={height} viewBox={`0 0 ${svgWidth} ${height}`}>
        {/* Zero line */}
        <line x1={0} y1={midY} x2={svgWidth} y2={midY} stroke="currentColor" strokeOpacity={0.2} strokeWidth={1} />
        {data.map((d, i) => {
          const barH = (Math.abs(d.value) / maxAbs) * (midY - 4)
          const y = d.value >= 0 ? midY - barH : midY
          return (
            <rect
              key={i}
              x={i * (barWidth + 1)}
              y={y}
              width={barWidth}
              height={Math.max(1, barH)}
              fill={d.value >= 0 ? positiveColor : negativeColor}
              opacity={0.8}
            >
              <title>{d.label}: {d.value.toFixed(4)}</title>
            </rect>
          )
        })}
      </svg>
    </div>
  )
}

/** Multi-line cumulative return chart using SVG. */
function CumulativeReturnChart({
  cumReturns,
}: {
  cumReturns: RollingResults['cumulative_returns']
}) {
  const { t } = useTranslation('admin')
  const height = 140
  const padding = { top: 10, right: 10, bottom: 20, left: 50 }

  const allPoints = [
    ...(cumReturns.q5 ?? []),
    ...(cumReturns.q1 ?? []),
    ...(cumReturns.spread ?? []),
  ]
  if (allPoints.length === 0) return null

  const allValues = allPoints.map(p => p.cumret)
  const minVal = Math.min(...allValues, 0)
  const maxVal = Math.max(...allValues, 0)
  const range = maxVal - minVal || 0.01

  const chartW = 600
  const chartH = height - padding.top - padding.bottom
  const svgW = chartW + padding.left + padding.right

  const lines: Array<{ data: Array<{ date: string; cumret: number }>; color: string; label: string }> = [
    { data: cumReturns.q5 ?? [], color: 'rgb(34, 197, 94)', label: t('predictions.backtest.topQuintile') },
    { data: cumReturns.q1 ?? [], color: 'rgb(239, 68, 68)', label: t('predictions.backtest.bottomQuintile') },
    { data: cumReturns.spread ?? [], color: 'rgb(59, 130, 246)', label: t('predictions.backtest.strategySpread') },
  ]

  function toPath(points: Array<{ date: string; cumret: number }>): string {
    if (points.length === 0) return ''
    return points.map((p, i) => {
      const x = padding.left + (i / Math.max(points.length - 1, 1)) * chartW
      const y = padding.top + (1 - (p.cumret - minVal) / range) * chartH
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
    }).join(' ')
  }

  // Zero line y position
  const zeroY = padding.top + (1 - (0 - minVal) / range) * chartH

  return (
    <div className="rounded border bg-background p-2">
      {/* Legend */}
      <div className="flex gap-4 mb-1 text-xs">
        {lines.map(l => l.data.length > 0 && (
          <div key={l.label} className="flex items-center gap-1">
            <div className="w-3 h-0.5 rounded" style={{ backgroundColor: l.color }} />
            <span>{l.label}</span>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto">
        <svg width={svgW} height={height} viewBox={`0 0 ${svgW} ${height}`}>
          {/* Zero line */}
          <line x1={padding.left} y1={zeroY} x2={padding.left + chartW} y2={zeroY} stroke="currentColor" strokeOpacity={0.2} strokeWidth={1} strokeDasharray="4 2" />
          <text x={padding.left - 4} y={zeroY + 3} textAnchor="end" fontSize={9} fill="currentColor" opacity={0.4}>0%</text>
          {/* Y axis labels */}
          <text x={padding.left - 4} y={padding.top + 4} textAnchor="end" fontSize={9} fill="currentColor" opacity={0.4}>{(maxVal * 100).toFixed(1)}%</text>
          <text x={padding.left - 4} y={padding.top + chartH + 3} textAnchor="end" fontSize={9} fill="currentColor" opacity={0.4}>{(minVal * 100).toFixed(1)}%</text>
          {/* Lines */}
          {lines.map(l => l.data.length > 1 && (
            <path key={l.label} d={toPath(l.data)} fill="none" stroke={l.color} strokeWidth={1.5} />
          ))}
        </svg>
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
