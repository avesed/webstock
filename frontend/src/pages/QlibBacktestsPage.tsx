import { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Loader2,
  AlertCircle,
  Trash2,
  XCircle,
  ArrowLeft,
  TrendingUp,
  TrendingDown,
  Clock,
  CheckCircle2,
  Ban,
  FlaskConical,
} from 'lucide-react'
import { createChart, type IChartApi } from 'lightweight-charts'

import { cn } from '@/lib/utils'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast, useFormatters } from '@/hooks'
import { qlibApi } from '@/api/qlib'
import { getErrorMessage } from '@/api/client'
import type {
  BacktestResponse,
  BacktestStatus,
  StrategyType,
  BacktestCreateRequest,
  BacktestResults,
} from '@/api/qlib'

// ---------------------------------------------------------------------------
// Status badge component
// ---------------------------------------------------------------------------

interface StatusBadgeProps {
  readonly status: BacktestStatus
}

function StatusBadge({ status }: StatusBadgeProps) {
  const { t } = useTranslation('common')

  const config: Record<BacktestStatus, { label: string; className: string; icon: React.ReactNode }> = {
    pending: {
      label: t('qlib.pending'),
      className: 'bg-yellow-100 text-yellow-800 border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400 dark:border-yellow-800',
      icon: <Clock className="h-3 w-3" />,
    },
    running: {
      label: t('qlib.running'),
      className: 'bg-blue-100 text-blue-800 border-blue-200 dark:bg-blue-900/30 dark:text-blue-400 dark:border-blue-800',
      icon: <Loader2 className="h-3 w-3 animate-spin" />,
    },
    completed: {
      label: t('qlib.completed'),
      className: 'bg-green-100 text-green-800 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800',
      icon: <CheckCircle2 className="h-3 w-3" />,
    },
    failed: {
      label: t('qlib.failed'),
      className: 'bg-red-100 text-red-800 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800',
      icon: <AlertCircle className="h-3 w-3" />,
    },
    cancelled: {
      label: t('qlib.cancelled'),
      className: 'bg-gray-100 text-gray-800 border-gray-200 dark:bg-gray-800/50 dark:text-gray-400 dark:border-gray-700',
      icon: <Ban className="h-3 w-3" />,
    },
  }

  const { label, className, icon } = config[status]

  return (
    <Badge variant="outline" className={cn('gap-1', className)}>
      {icon}
      {label}
    </Badge>
  )
}

// ---------------------------------------------------------------------------
// Strategy label helper
// ---------------------------------------------------------------------------

function useStrategyLabel() {
  const { t } = useTranslation('common')
  return useCallback(
    (type: StrategyType): string => {
      const labels: Record<StrategyType, string> = {
        topk: t('qlib.topk'),
        signal: t('qlib.signal'),
        long_short: t('qlib.longShort'),
      }
      return labels[type]
    },
    [t],
  )
}

// ---------------------------------------------------------------------------
// Equity curve chart component
// ---------------------------------------------------------------------------

interface EquityCurveChartProps {
  readonly data: BacktestResults['equityCurve']
}

function EquityCurveChart({ data }: EquityCurveChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ReturnType<IChartApi['addLineSeries']> | null>(null)

  useEffect(() => {
    if (!containerRef.current || data.length === 0) return

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 350,
      layout: {
        background: { color: 'transparent' },
        textColor: getComputedStyle(document.documentElement)
          .getPropertyValue('--foreground')
          .trim() || '#333',
        fontFamily: 'inherit',
      },
      grid: {
        vertLines: { color: 'rgba(128, 128, 128, 0.1)' },
        horzLines: { color: 'rgba(128, 128, 128, 0.1)' },
      },
      rightPriceScale: {
        borderVisible: false,
      },
      timeScale: {
        borderVisible: false,
        timeVisible: false,
      },
      crosshair: {
        horzLine: { visible: true, labelVisible: true },
        vertLine: { visible: true, labelVisible: true },
      },
    })

    chartRef.current = chart

    const series = chart.addLineSeries({
      color: '#2563eb',
      lineWidth: 2,
      crosshairMarkerVisible: true,
      priceFormat: { type: 'price', precision: 4, minMove: 0.0001 },
    })

    seriesRef.current = series

    const chartData = data.map((point) => ({
      time: point.date as string,
      value: point.value,
    }))

    series.setData(chartData)
    chart.timeScale().fitContent()

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth })
      }
    }

    const resizeObserver = new ResizeObserver(handleResize)
    resizeObserver.observe(containerRef.current)

    return () => {
      resizeObserver.disconnect()
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [data])

  return <div ref={containerRef} className="w-full" />
}

// ---------------------------------------------------------------------------
// Risk metrics grid component
// ---------------------------------------------------------------------------

interface RiskMetricsGridProps {
  readonly results: BacktestResults
}

function RiskMetricsGrid({ results }: RiskMetricsGridProps) {
  const { t } = useTranslation('common')
  const { formatNumber, formatPercent } = useFormatters()

  const metrics = [
    {
      label: t('qlib.totalReturn'),
      value: formatPercent(results.totalReturn * 100),
      icon: results.totalReturn >= 0 ? TrendingUp : TrendingDown,
      positive: results.totalReturn >= 0,
    },
    {
      label: t('qlib.annualReturn'),
      value: formatPercent(results.annualReturn * 100),
      icon: results.annualReturn >= 0 ? TrendingUp : TrendingDown,
      positive: results.annualReturn >= 0,
    },
    {
      label: t('qlib.sharpeRatio'),
      value: formatNumber(results.sharpeRatio),
      positive: results.sharpeRatio >= 1,
    },
    {
      label: t('qlib.maxDrawdown'),
      value: formatPercent(Math.abs(results.maxDrawdown) * 100),
      positive: false,
    },
    {
      label: t('qlib.annualVolatility'),
      value: formatPercent(results.annualVolatility * 100),
    },
    {
      label: t('qlib.calmarRatio'),
      value: formatNumber(results.calmarRatio),
      positive: results.calmarRatio >= 1,
    },
    {
      label: t('qlib.winRate'),
      value: formatPercent(results.winRate * 100),
      positive: results.winRate >= 0.5,
    },
    {
      label: t('qlib.profitLossRatio'),
      value: formatNumber(results.profitLossRatio),
      positive: results.profitLossRatio >= 1,
    },
    {
      label: t('qlib.totalTrades'),
      value: String(results.totalTrades),
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
      {metrics.map((metric) => (
        <div key={metric.label} className="space-y-1 rounded-lg border p-3">
          <p className="text-xs text-muted-foreground">{metric.label}</p>
          <p
            className={cn(
              'text-lg font-semibold',
              metric.positive === true && 'text-stock-up',
              metric.positive === false && 'text-stock-down',
            )}
          >
            {metric.value}
          </p>
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Backtest detail view
// ---------------------------------------------------------------------------

interface BacktestDetailProps {
  readonly backtestId: string
  readonly onBack: () => void
}

function BacktestDetail({ backtestId, onBack }: BacktestDetailProps) {
  const { t } = useTranslation('common')
  const { toast } = useToast()
  const { formatDate } = useFormatters()
  const queryClient = useQueryClient()
  const getStrategyLabel = useStrategyLabel()

  const isActiveStatus = (status: BacktestStatus) =>
    status === 'pending' || status === 'running'

  const { data: backtest, isLoading, error } = useQuery({
    queryKey: ['qlib-backtest', backtestId],
    queryFn: () => qlibApi.getBacktest(backtestId),
    refetchInterval: (query) => {
      const bt = query.state.data
      if (bt && isActiveStatus(bt.status)) {
        return 3000
      }
      return false
    },
  })

  const cancelMutation = useMutation({
    mutationFn: () => qlibApi.cancelBacktest(backtestId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['qlib-backtest', backtestId] })
      queryClient.invalidateQueries({ queryKey: ['qlib-backtests'] })
      toast({ title: t('status.success'), description: t('qlib.cancelled') })
    },
    onError: (err: unknown) => {
      toast({ title: t('status.error'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: () => qlibApi.deleteBacktest(backtestId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['qlib-backtests'] })
      toast({ title: t('status.success'), description: t('qlib.delete') })
      onBack()
    },
    onError: (err: unknown) => {
      toast({ title: t('status.error'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[350px] w-full" />
        <div className="grid grid-cols-3 gap-4">
          {Array.from({ length: 9 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      </div>
    )
  }

  if (error || !backtest) {
    return (
      <div className="flex flex-col items-center justify-center gap-4 py-16">
        <AlertCircle className="h-12 w-12 text-destructive" />
        <p className="text-muted-foreground">{t('status.error')}</p>
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          {t('actions.back')}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" onClick={onBack}>
            <ArrowLeft className="h-5 w-5" />
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-2xl font-bold tracking-tight">{backtest.name}</h2>
              <StatusBadge status={backtest.status} />
            </div>
            <p className="text-sm text-muted-foreground">
              {getStrategyLabel(backtest.strategyType)} &middot;{' '}
              {backtest.market} &middot;{' '}
              {backtest.startDate} ~ {backtest.endDate}
            </p>
          </div>
        </div>

        <div className="flex gap-2">
          {isActiveStatus(backtest.status) && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => cancelMutation.mutate()}
              disabled={cancelMutation.isPending}
            >
              {cancelMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <XCircle className="mr-2 h-4 w-4" />
              )}
              {t('qlib.cancel')}
            </Button>
          )}
          {!isActiveStatus(backtest.status) && (
            <Button
              variant="destructive"
              size="sm"
              onClick={() => deleteMutation.mutate()}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="mr-2 h-4 w-4" />
              )}
              {t('qlib.delete')}
            </Button>
          )}
        </div>
      </div>

      {/* Progress bar for running */}
      {isActiveStatus(backtest.status) && (
        <Card>
          <CardContent className="pt-6">
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">{t('status.processing')}</span>
                <span className="font-medium">{backtest.progress}%</span>
              </div>
              <Progress value={backtest.progress} className="h-2" />
            </div>
          </CardContent>
        </Card>
      )}

      {/* Error display */}
      {backtest.status === 'failed' && backtest.error && (
        <Card className="border-destructive">
          <CardContent className="pt-6">
            <div className="flex items-start gap-3">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
              <div>
                <p className="font-medium text-destructive">{t('status.failed')}</p>
                <p className="mt-1 text-sm text-muted-foreground">{backtest.error}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Symbols */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">{t('qlib.stockSymbols')}</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-1.5">
            {backtest.symbols.map((sym) => (
              <Badge key={sym} variant="secondary">{sym}</Badge>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Results section */}
      {backtest.results && (
        <>
          {/* Equity Curve */}
          <Card>
            <CardHeader>
              <CardTitle>{t('qlib.equityCurve')}</CardTitle>
              <CardDescription>
                {backtest.startDate} ~ {backtest.endDate}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {backtest.results.equityCurve.length > 0 ? (
                <EquityCurveChart data={backtest.results.equityCurve} />
              ) : (
                <div className="flex h-[350px] items-center justify-center text-muted-foreground">
                  {t('status.noData')}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Risk Metrics */}
          <Card>
            <CardHeader>
              <CardTitle>{t('qlib.riskMetrics')}</CardTitle>
            </CardHeader>
            <CardContent>
              <RiskMetricsGrid results={backtest.results} />
            </CardContent>
          </Card>

          {/* Max drawdown period */}
          {backtest.results.maxDrawdownPeriod && (
            <Card>
              <CardContent className="pt-6">
                <p className="text-sm text-muted-foreground">
                  {t('qlib.maxDrawdown')}: {formatDate(backtest.results.maxDrawdownPeriod.start)} ~ {formatDate(backtest.results.maxDrawdownPeriod.end)}
                </p>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {/* Metadata */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm text-muted-foreground">
            <span>{t('actions.create')}: {formatDate(backtest.createdAt)}</span>
            {backtest.completedAt && (
              <span>{t('status.completed')}: {formatDate(backtest.completedAt)}</span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Create backtest dialog
// ---------------------------------------------------------------------------

interface CreateBacktestDialogProps {
  readonly open: boolean
  readonly onOpenChange: (open: boolean) => void
}

function CreateBacktestDialog({ open, onOpenChange }: CreateBacktestDialogProps) {
  const { t } = useTranslation('common')
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [market, setMarket] = useState('US')
  const [symbolsInput, setSymbolsInput] = useState('')
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')
  const [strategyType, setStrategyType] = useState<StrategyType>('topk')

  // TopK params
  const [topK, setTopK] = useState('10')
  const [nDrop, setNDrop] = useState('5')

  // Signal params
  const [expression, setExpression] = useState('')
  const [threshold, setThreshold] = useState('0')

  const resetForm = useCallback(() => {
    setName('')
    setMarket('US')
    setSymbolsInput('')
    setStartDate('')
    setEndDate('')
    setStrategyType('topk')
    setTopK('10')
    setNDrop('5')
    setExpression('')
    setThreshold('0')
  }, [])

  const createMutation = useMutation({
    mutationFn: (request: BacktestCreateRequest) => qlibApi.createBacktest(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['qlib-backtests'] })
      toast({ title: t('status.success'), description: t('qlib.createBacktest') })
      onOpenChange(false)
      resetForm()
    },
    onError: (err: unknown) => {
      toast({ title: t('status.error'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const handleSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault()

      const symbols = symbolsInput
        .split(',')
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean)

      if (symbols.length === 0) return

      let strategyConfig: Record<string, unknown> = {}
      if (strategyType === 'topk') {
        strategyConfig = { k: Number(topK), n_drop: Number(nDrop) }
      } else if (strategyType === 'signal') {
        strategyConfig = { expression, threshold: Number(threshold) }
      }
      // long_short has no extra config by default

      const request: BacktestCreateRequest = {
        name,
        market,
        symbols,
        startDate,
        endDate,
        strategyType,
        strategyConfig,
      }

      createMutation.mutate(request)
    },
    [name, market, symbolsInput, startDate, endDate, strategyType, topK, nDrop, expression, threshold, createMutation],
  )

  const isFormValid = useMemo(() => {
    const hasName = name.trim().length > 0
    const hasSymbols = symbolsInput.trim().length > 0
    const hasDates = startDate.length > 0 && endDate.length > 0
    return hasName && hasSymbols && hasDates
  }, [name, symbolsInput, startDate, endDate])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('qlib.createBacktest')}</DialogTitle>
          <DialogDescription>
            {t('qlib.symbolsHelp')}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-2">
            <Label htmlFor="bt-name">{t('qlib.newBacktest')}</Label>
            <Input
              id="bt-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t('qlib.backtestNamePlaceholder')}
              required
            />
          </div>

          {/* Market */}
          <div className="space-y-2">
            <Label>{t('qlib.market')}</Label>
            <Select value={market} onValueChange={setMarket}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="US">US</SelectItem>
                <SelectItem value="HK">HK</SelectItem>
                <SelectItem value="CN">CN</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Symbols */}
          <div className="space-y-2">
            <Label htmlFor="bt-symbols">{t('qlib.stockSymbols')}</Label>
            <Input
              id="bt-symbols"
              value={symbolsInput}
              onChange={(e) => setSymbolsInput(e.target.value)}
              placeholder="AAPL, MSFT, GOOGL"
              required
            />
            <p className="text-xs text-muted-foreground">
              {t('qlib.symbolsHelp')}
            </p>
          </div>

          {/* Date range */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-2">
              <Label htmlFor="bt-start">{t('qlib.startDate')}</Label>
              <Input
                id="bt-start"
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="bt-end">{t('qlib.endDate')}</Label>
              <Input
                id="bt-end"
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                required
              />
            </div>
          </div>

          {/* Strategy type */}
          <div className="space-y-2">
            <Label>{t('qlib.strategyType')}</Label>
            <Select
              value={strategyType}
              onValueChange={(v) => setStrategyType(v as StrategyType)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="topk">{t('qlib.topk')}</SelectItem>
                <SelectItem value="signal">{t('qlib.signal')}</SelectItem>
                <SelectItem value="long_short">{t('qlib.longShort')}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Strategy-specific params */}
          {strategyType === 'topk' && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label htmlFor="bt-topk">{t('qlib.topkK')}</Label>
                <Input
                  id="bt-topk"
                  type="number"
                  min="1"
                  value={topK}
                  onChange={(e) => setTopK(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="bt-ndrop">{t('qlib.nDrop')}</Label>
                <Input
                  id="bt-ndrop"
                  type="number"
                  min="0"
                  value={nDrop}
                  onChange={(e) => setNDrop(e.target.value)}
                />
              </div>
            </div>
          )}

          {strategyType === 'signal' && (
            <div className="space-y-3">
              <div className="space-y-2">
                <Label htmlFor="bt-expression">{t('qlib.expression')}</Label>
                <Input
                  id="bt-expression"
                  value={expression}
                  onChange={(e) => setExpression(e.target.value)}
                  placeholder={t('qlib.expressionPlaceholder')}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="bt-threshold">{t('qlib.threshold')}</Label>
                <Input
                  id="bt-threshold"
                  type="number"
                  step="0.01"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                />
              </div>
            </div>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('actions.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={!isFormValid || createMutation.isPending}
            >
              {createMutation.isPending && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {t('actions.create')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Backtest list item
// ---------------------------------------------------------------------------

interface BacktestListItemProps {
  readonly backtest: BacktestResponse
  readonly onClick: () => void
}

function BacktestListItem({ backtest, onClick }: BacktestListItemProps) {
  const { t } = useTranslation('common')
  const { formatDate, formatPercent } = useFormatters()
  const getStrategyLabel = useStrategyLabel()

  const isActive = backtest.status === 'pending' || backtest.status === 'running'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
      className="flex flex-col gap-3 rounded-lg border p-4 transition-colors hover:bg-accent/50 cursor-pointer sm:flex-row sm:items-center sm:justify-between"
    >
      <div className="flex-1 space-y-1">
        <div className="flex items-center gap-2">
          <span className="font-medium">{backtest.name}</span>
          <StatusBadge status={backtest.status} />
          <Badge variant="outline" className="text-xs">
            {backtest.market}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          {getStrategyLabel(backtest.strategyType)} &middot;{' '}
          {backtest.symbols.slice(0, 5).join(', ')}
          {backtest.symbols.length > 5 ? ` +${backtest.symbols.length - 5}` : ''}
        </p>
        <p className="text-xs text-muted-foreground">
          {backtest.startDate} ~ {backtest.endDate}
          {backtest.createdAt && (
            <> &middot; {formatDate(backtest.createdAt)}</>
          )}
        </p>
      </div>

      <div className="flex items-center gap-4">
        {/* Progress or result summary */}
        {isActive && (
          <div className="w-32">
            <div className="mb-1 flex justify-between text-xs">
              <span className="text-muted-foreground">{backtest.progress}%</span>
            </div>
            <Progress value={backtest.progress} className="h-1.5" />
          </div>
        )}

        {backtest.status === 'completed' && backtest.results && (
          <div className="text-right">
            <p
              className={cn(
                'text-sm font-semibold',
                backtest.results.totalReturn >= 0 ? 'text-stock-up' : 'text-stock-down',
              )}
            >
              {formatPercent(backtest.results.totalReturn * 100)}
            </p>
            <p className="text-xs text-muted-foreground">
              {t('qlib.sharpeRatio')} {backtest.results.sharpeRatio.toFixed(2)}
            </p>
          </div>
        )}

        {backtest.status === 'failed' && (
          <AlertCircle className="h-5 w-5 text-destructive" />
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function QlibBacktestsPage() {
  const { t } = useTranslation('common')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const hasAnyActive = (backtests: BacktestResponse[]) =>
    backtests.some((bt) => bt.status === 'pending' || bt.status === 'running')

  const {
    data: backtests,
    isLoading,
    error,
  } = useQuery({
    queryKey: ['qlib-backtests'],
    queryFn: () => qlibApi.getBacktests(50, 0),
    refetchInterval: (query) => {
      const list = query.state.data
      if (list && hasAnyActive(list)) {
        return 5000
      }
      return false
    },
  })

  // Detail view
  if (selectedId) {
    return (
      <div className="space-y-6">
        <BacktestDetail
          backtestId={selectedId}
          onBack={() => setSelectedId(null)}
        />
      </div>
    )
  }

  // List view
  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{t('qlib.backtests')}</h1>
          <p className="text-muted-foreground">
            {t('qlib.backtestsDescription')}
          </p>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          <Plus className="mr-2 h-4 w-4" />
          {t('qlib.newBacktest')}
        </Button>
      </div>

      {/* Loading state */}
      {isLoading && (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      )}

      {/* Error state */}
      {error && (
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 pt-6">
            <AlertCircle className="h-5 w-5 shrink-0 text-destructive" />
            <p className="text-sm text-destructive">{getErrorMessage(error)}</p>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && !error && backtests && backtests.length === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center gap-4 py-16">
            <FlaskConical className="h-12 w-12 text-muted-foreground" />
            <div className="text-center">
              <h3 className="text-lg font-semibold">{t('empty.title')}</h3>
              <p className="text-sm text-muted-foreground">{t('empty.description')}</p>
            </div>
            <Button onClick={() => setDialogOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              {t('qlib.newBacktest')}
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Backtest list */}
      {!isLoading && backtests && backtests.length > 0 && (
        <div className="space-y-2">
          {backtests.map((bt) => (
            <BacktestListItem
              key={bt.id}
              backtest={bt}
              onClick={() => setSelectedId(bt.id)}
            />
          ))}
        </div>
      )}

      {/* Create dialog */}
      <CreateBacktestDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  )
}
