/**
 * User-facing AI Predictions page.
 *
 * Displays ML model stock prediction rankings with a UX-first design:
 * - No quant jargon (IC/ICIR hidden; hit rate and rank bars instead)
 * - Bullish/bearish split with colour-coded rank progress bars
 * - Clickable symbols → stock detail page
 * - Collapsible performance trends for advanced users
 */

import { useState, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  TrendingUp,
  TrendingDown,
  BarChart3,
  Activity,
  Brain,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  AlertCircle,
  Loader2,
} from 'lucide-react'

import {
  getLatestPredictions,
  getPredictionSummary,
  getPerformanceTrends,
} from '@/api/userPredictions'
import type { PredictionSummary } from '@/api/userPredictions'
import type { PredictionResult, ModelPerformanceMetric } from '@/api/predictions'
import { getErrorMessage } from '@/api/client'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useFormatters } from '@/hooks/useFormatters'
import { cn } from '@/lib/utils'

const MARKETS = ['cn', 'us', 'hk'] as const
type MarketKey = (typeof MARKETS)[number]

const DEFAULT_MARKET: MarketKey = 'cn'
const INITIAL_DISPLAY = 20
const BULLISH_THRESHOLD = 0.70
const BEARISH_THRESHOLD = 0.30

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const td = (t: (k: any, opts?: any) => string, key: string, opts?: Record<string, unknown>): string =>
  opts ? t(key, opts) : t(key)

// ── Sub-components ───────────────────────────────

function SummaryCards({
  summary,
  totalCount,
  hasDirectionModel,
  t,
}: {
  summary: PredictionSummary | undefined
  totalCount: number
  hasDirectionModel: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (k: any, opts?: any) => string
}) {
  return (
    <div className="grid gap-4 md:grid-cols-4">
      {/* Stock count */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <BarChart3 className="h-4 w-4" />
            {td(t, 'predictions.stockCount')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">
            {totalCount}
            <span className="ml-1 text-sm font-normal text-muted-foreground">
              {td(t, 'predictions.stocks')}
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Model status */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Activity className="h-4 w-4" />
            {td(t, 'predictions.modelStatus')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {summary?.model ? (
            <div className="flex items-center gap-2">
              {summary.model.qualityPassed ? (
                <CheckCircle2 className="h-5 w-5 text-stock-up" />
              ) : (
                <XCircle className="h-5 w-5 text-stock-down" />
              )}
              <span className="text-sm font-medium">
                {td(t, summary.model.qualityPassed ? 'predictions.qualityPassed' : 'predictions.qualityFailed')}
              </span>
            </div>
          ) : (
            <span className="text-sm text-muted-foreground">-</span>
          )}
          {summary?.model?.modelDate && (
            <p className="mt-1 text-xs text-muted-foreground">{summary.model.modelDate}</p>
          )}
        </CardContent>
      </Card>

      {/* Direction model status */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <Brain className="h-4 w-4" />
            {td(t, 'predictions.directionModel')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {hasDirectionModel ? (
            <Badge className="bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300">
              {td(t, 'predictions.directionModel')}: {td(t, 'predictions.qualityPassed')}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-600">
              {td(t, 'predictions.directionNotTrained')}
            </Badge>
          )}
        </CardContent>
      </Card>

      {/* Recent performance */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
            <TrendingUp className="h-4 w-4" />
            {td(t, 'predictions.recentPerformance')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {summary?.accuracy ? (
            <div>
              <span className="text-2xl font-bold">
                {(summary.accuracy.hitRate * 100).toFixed(1)}%
              </span>
              <span className="ml-2 text-sm text-muted-foreground">
                {td(t, 'predictions.hitRate')}
              </span>
            </div>
          ) : (
            <span className="text-sm text-muted-foreground">-</span>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function RankBar({ value, variant }: { value: number; variant: 'bullish' | 'bearish' }) {
  return (
    <div className="h-2 w-full rounded-full bg-muted">
      <div
        className={cn(
          'h-full rounded-full transition-all duration-500',
          variant === 'bullish' ? 'bg-stock-up' : 'bg-stock-down'
        )}
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}

function DirectionBadge({
  direction,
  upProbability,
  t,
}: {
  direction: PredictionResult['predictedDirection']
  upProbability?: number | null | undefined
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (k: any) => string
}) {
  const NEUTRAL_LO = 0.45
  const NEUTRAL_HI = 0.55
  const greenCls = 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300'
  const redCls = 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300'
  const grayCls = 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300'

  const config = {
    up: { label: td(t, 'predictions.bullish'), cls: greenCls },
    down: { label: td(t, 'predictions.bearish'), cls: redCls },
    sideways: { label: td(t, 'predictions.neutral'), cls: grayCls },
  }
  const c = config[direction]
  if (upProbability != null) {
    const pctText = `${(upProbability * 100).toFixed(0)}%`
    const probCls = upProbability > NEUTRAL_HI
      ? greenCls
      : upProbability < NEUTRAL_LO
        ? redCls
        : grayCls
    return <Badge className={probCls}>{c.label} {pctText}</Badge>
  }
  return <Badge className={c.cls}>{c.label}</Badge>
}

function PredictionItem({
  prediction,
  variant,
  t,
}: {
  prediction: PredictionResult
  variant: 'bullish' | 'bearish'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (k: any) => string
}) {
  return (
    <div className="flex items-center gap-3 rounded-lg border bg-card px-4 py-3 transition-colors hover:bg-accent/50">
      {/* Symbol — clickable */}
      <Link
        to={`/stock/${prediction.symbol}`}
        className="w-24 shrink-0 font-mono text-sm font-semibold text-primary hover:underline"
      >
        {prediction.symbol}
      </Link>

      {/* Rank bar */}
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <div className="flex-1">
          <RankBar value={prediction.percentileRank * 100} variant={variant} />
        </div>
        <span className={cn(
          'w-12 shrink-0 text-right text-sm font-medium',
          variant === 'bullish' ? 'text-stock-up' : 'text-stock-down'
        )}>
          {(prediction.percentileRank * 100).toFixed(0)}%
        </span>
      </div>

      {/* Direction badge */}
      <DirectionBadge direction={prediction.predictedDirection} upProbability={prediction.upProbability} t={t} />
    </div>
  )
}

function PredictionSection({
  title,
  icon,
  predictions,
  total,
  expanded,
  onToggle,
  variant,
  t,
}: {
  title: string
  icon: React.ReactNode
  predictions: PredictionResult[]
  total: number
  expanded: boolean
  onToggle: () => void
  variant: 'bullish' | 'bearish'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (k: any, opts?: any) => string
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-lg font-semibold">{title}</h2>
        <span className="text-sm text-muted-foreground">TOP {predictions.length}</span>
      </div>

      <div className="space-y-2">
        {predictions.map(p => (
          <PredictionItem key={p.symbol} prediction={p} variant={variant} t={t} />
        ))}
      </div>

      {total > INITIAL_DISPLAY && (
        <Button variant="ghost" size="sm" onClick={onToggle} className="w-full">
          {expanded
            ? td(t, 'predictions.showLess')
            : td(t, 'predictions.showAll', { count: total })}
        </Button>
      )}
    </div>
  )
}

function PerformanceTable({
  metrics,
  t,
  formatPercent,
}: {
  metrics: ModelPerformanceMetric[]
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (k: any) => string
  formatPercent: (v: number) => string
}) {
  if (metrics.length === 0) {
    return (
      <p className="text-center text-sm text-muted-foreground">
        {td(t, 'predictions.noData')}
      </p>
    )
  }

  // Show last 20 data points, most recent first
  const recent = metrics.slice(-20).reverse()

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left text-muted-foreground">
            <th className="pb-2 pr-4 font-medium">{td(t, 'predictions.date')}</th>
            <th className="pb-2 pr-4 font-medium">{td(t, 'predictions.hitRate')}</th>
            <th className="pb-2 font-medium">{td(t, 'predictions.spread')}</th>
          </tr>
        </thead>
        <tbody>
          {recent.map(m => (
            <tr key={m.date} className="border-b border-border/50">
              <td className="py-2 pr-4 font-mono text-xs">{m.date}</td>
              <td className="py-2 pr-4">
                {m.hitRate != null ? (
                  <span className={cn(
                    'font-medium',
                    m.hitRate >= 0.5 ? 'text-stock-up' : 'text-stock-down'
                  )}>
                    {formatPercent(m.hitRate * 100)}
                  </span>
                ) : '-'}
              </td>
              <td className="py-2">
                <span className={cn(
                  'font-medium',
                  m.spread >= 0 ? 'text-stock-up' : 'text-stock-down'
                )}>
                  {m.spread >= 0 ? '+' : ''}{formatPercent(m.spread * 100)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main Page ────────────────────────────────────

export default function PredictionsPage() {
  const { t } = useTranslation('common')
  const { formatPercent } = useFormatters()
  const [searchParams, setSearchParams] = useSearchParams()
  const [expandedBullish, setExpandedBullish] = useState(false)
  const [expandedBearish, setExpandedBearish] = useState(false)
  const [showPerformance, setShowPerformance] = useState(false)

  const market = (searchParams.get('market') ?? DEFAULT_MARKET) as MarketKey

  const setMarket = (m: MarketKey) => {
    setSearchParams({ market: m })
    setExpandedBullish(false)
    setExpandedBearish(false)
  }

  // ── Queries ──────────────────────────────────

  const {
    data: latestData,
    isLoading: loadingLatest,
    error: latestError,
  } = useQuery({
    queryKey: ['user-predictions', market, 'latest'],
    queryFn: () => getLatestPredictions(market, 500),
    staleTime: 5 * 60_000,
  })

  const {
    data: summary,
    isLoading: loadingSummary,
  } = useQuery({
    queryKey: ['user-predictions', market, 'summary'],
    queryFn: () => getPredictionSummary(market),
    staleTime: 5 * 60_000,
  })

  const {
    data: performance,
    isLoading: loadingPerformance,
  } = useQuery({
    queryKey: ['user-predictions', market, 'performance'],
    queryFn: () => getPerformanceTrends(market, 60),
    staleTime: 10 * 60_000,
    enabled: showPerformance,
  })

  // ── Derived data ─────────────────────────────

  const { bullish, bearish, hasDirectionModel } = useMemo(() => {
    const predictions = latestData?.predictions ?? []
    return {
      bullish: predictions
        .filter(p => p.percentileRank >= BULLISH_THRESHOLD)
        .sort((a, b) => b.percentileRank - a.percentileRank),
      bearish: predictions
        .filter(p => p.percentileRank <= BEARISH_THRESHOLD)
        .sort((a, b) => a.percentileRank - b.percentileRank),
      hasDirectionModel: predictions.some(p => p.upProbability != null),
    }
  }, [latestData])

  const displayBullish = expandedBullish ? bullish : bullish.slice(0, INITIAL_DISPLAY)
  const displayBearish = expandedBearish ? bearish : bearish.slice(0, INITIAL_DISPLAY)

  const isLoading = loadingLatest || loadingSummary

  // ── Render ───────────────────────────────────

  return (
    <div className="space-y-6 pb-20 lg:pb-0">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            {td(t, 'predictions.title')}
          </h1>
          <p className="text-sm text-muted-foreground">
            {td(t, 'predictions.subtitle')}
          </p>
        </div>
        {summary?.predictionDate && (
          <span className="text-xs text-muted-foreground">
            {td(t, 'predictions.lastUpdated')}: {summary.predictionDate}
          </span>
        )}
      </div>

      {/* Market tabs */}
      <div className="flex gap-2">
        {MARKETS.map(m => (
          <Button
            key={m}
            variant={market === m ? 'default' : 'outline'}
            size="sm"
            onClick={() => setMarket(m)}
          >
            {td(t, `predictions.market_${m}`)}
          </Button>
        ))}
      </div>

      {/* Summary cards */}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-4">
          {[1, 2, 3, 4].map(i => (
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
      ) : (
        <SummaryCards
          summary={summary}
          totalCount={latestData?.predictions.length ?? 0}
          hasDirectionModel={hasDirectionModel}
          t={t}
        />
      )}

      {/* Error state */}
      {latestError && (
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 py-6">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <span className="text-sm text-destructive">
              {getErrorMessage(latestError)}
            </span>
          </CardContent>
        </Card>
      )}

      {/* Empty state */}
      {!isLoading && !latestError && (latestData?.predictions.length ?? 0) === 0 && (
        <Card>
          <CardContent className="flex flex-col items-center justify-center py-12">
            <BarChart3 className="h-12 w-12 text-muted-foreground/40" />
            <p className="mt-4 text-sm text-muted-foreground">
              {td(t, 'predictions.noData')}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Bullish rankings */}
      {displayBullish.length > 0 && (
        <PredictionSection
          title={td(t, 'predictions.topRankings')}
          icon={<TrendingUp className="h-5 w-5 text-stock-up" />}
          predictions={displayBullish}
          total={bullish.length}
          expanded={expandedBullish}
          onToggle={() => setExpandedBullish(!expandedBullish)}
          variant="bullish"
          t={t}
        />
      )}

      {/* Bearish rankings */}
      {displayBearish.length > 0 && (
        <PredictionSection
          title={td(t, 'predictions.bottomRankings')}
          icon={<TrendingDown className="h-5 w-5 text-stock-down" />}
          predictions={displayBearish}
          total={bearish.length}
          expanded={expandedBearish}
          onToggle={() => setExpandedBearish(!expandedBearish)}
          variant="bearish"
          t={t}
        />
      )}

      {/* Performance trends (collapsible) */}
      <div>
        <Button
          variant="ghost"
          className="flex items-center gap-2 text-muted-foreground hover:text-foreground"
          onClick={() => setShowPerformance(!showPerformance)}
        >
          {showPerformance
            ? <ChevronDown className="h-4 w-4" />
            : <ChevronRight className="h-4 w-4" />}
          {td(t, 'predictions.performanceTrends')}
        </Button>
        {showPerformance && (
          <Card className="mt-2">
            <CardContent className="py-6">
              {loadingPerformance ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : performance ? (
                <PerformanceTable
                  metrics={performance.metrics}
                  t={t}
                  formatPercent={formatPercent}
                />
              ) : (
                <p className="text-center text-sm text-muted-foreground">
                  {td(t, 'predictions.noData')}
                </p>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
