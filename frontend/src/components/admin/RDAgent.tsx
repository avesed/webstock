import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Play, Square, Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'

import { predictionsApi } from '@/api/predictions'
import type { RDAgentStatus, DiscoveredFactor } from '@/api/predictions'
import { getErrorMessage } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Progress } from '@/components/ui/progress'
import { useToast } from '@/hooks/useToast'
import { cn } from '@/lib/utils'

// Dynamic i18n key helper
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const td = (t: (k: any, opts?: any) => string, key: string, opts?: Record<string, unknown>): string =>
  opts ? t(key, opts) : t(key)

const MARKETS = ['cn', 'us', 'hk'] as const
type MarketKey = typeof MARKETS[number]

// ── Status Badge ───────────────────────────────

function StatusBadge({ status }: { status: RDAgentStatus['status'] }) {
  const { t } = useTranslation('admin')
  const variantMap: Record<RDAgentStatus['status'], { variant: 'default' | 'secondary' | 'destructive'; icon: React.ReactNode }> = {
    idle: { variant: 'secondary', icon: null },
    starting: { variant: 'secondary', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    running: { variant: 'default', icon: <Loader2 className="h-3 w-3 animate-spin" /> },
    completed: { variant: 'default', icon: <CheckCircle2 className="h-3 w-3" /> },
    failed: { variant: 'destructive', icon: <XCircle className="h-3 w-3" /> },
    stopped: { variant: 'secondary', icon: <Square className="h-3 w-3" /> },
  }
  const m = variantMap[status]
  return (
    <Badge variant={m.variant} className="gap-1">
      {m.icon}
      {td(t, `predictions.status_${status}`)}
    </Badge>
  )
}

// ── Market RD-Agent Card ───────────────────────

function MarketRDAgentCard({ market }: { market: MarketKey }) {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const { data: status, isLoading } = useQuery({
    queryKey: ['admin', 'rdagent', 'status', market],
    queryFn: () => predictionsApi.getRDAgentStatus(market),
    refetchInterval: (query) => {
      const s = query.state.data
      if (s && (s.status === 'running' || s.status === 'starting')) return 5000
      return false
    },
    staleTime: 10_000,
  })

  const startMutation = useMutation({
    mutationFn: () => predictionsApi.startRDAgent(market, 30),
    onSuccess: () => {
      toast({ title: td(t, 'predictions.rdAgentStarted') })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'rdagent', 'status', market] })
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.rdAgentError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const stopMutation = useMutation({
    mutationFn: () => predictionsApi.stopRDAgent(market),
    onSuccess: () => {
      toast({ title: td(t, 'predictions.rdAgentStopped') })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'rdagent', 'status', market] })
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.rdAgentError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  const isRunning = status?.status === 'running' || status?.status === 'starting'
  const progressPercent = status && status.maxRounds > 0
    ? Math.round((status.currentRound / status.maxRounds) * 100)
    : 0

  if (isLoading) {
    return <Skeleton className="h-32" />
  }

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-medium">{td(t, `predictions.market_${market}`)}</span>
        {status && <StatusBadge status={status.status} />}
      </div>

      {status && isRunning && (
        <>
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>{td(t, 'predictions.round', { current: status.currentRound, max: status.maxRounds })}</span>
              <span>{progressPercent}%</span>
            </div>
            <Progress value={progressPercent} className="h-2" />
          </div>
          <div className="text-xs text-muted-foreground">
            {td(t, 'predictions.discovered', { count: status.discoveredCount })}
          </div>
        </>
      )}

      {status?.error && (
        <p className="text-xs text-destructive flex items-start gap-1">
          <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
          {status.error}
        </p>
      )}

      {status?.status === 'completed' && (
        <div className="text-xs text-muted-foreground space-y-1">
          <p>{td(t, 'predictions.discovered', { count: status.discoveredCount })}</p>
          {status.completedAt && (
            <p>{td(t, 'predictions.completedAt', { time: new Date(status.completedAt).toLocaleString() })}</p>
          )}
        </div>
      )}

      <div className="flex gap-2">
        {isRunning ? (
          <Button
            size="sm"
            variant="destructive"
            disabled={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
          >
            {stopMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin mr-1" />
            ) : (
              <Square className="h-3 w-3 mr-1" />
            )}
            {td(t, 'predictions.stop')}
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={startMutation.isPending}
            onClick={() => startMutation.mutate()}
          >
            {startMutation.isPending ? (
              <Loader2 className="h-3 w-3 animate-spin mr-1" />
            ) : (
              <Play className="h-3 w-3 mr-1" />
            )}
            {td(t, 'predictions.start')}
          </Button>
        )}
      </div>
    </div>
  )
}

// ── Discovered Factors ─────────────────────────

function DiscoveredFactorsTable() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const { data: factors, isLoading } = useQuery({
    queryKey: ['admin', 'predictions', 'factors'],
    queryFn: () => predictionsApi.getFactors(),
    staleTime: 30_000,
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: string; isActive: boolean }) =>
      predictionsApi.toggleFactor(id, isActive),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['admin', 'predictions', 'factors'] })
    },
    onError: (err) => {
      toast({ title: td(t, 'predictions.toggleError'), description: getErrorMessage(err), variant: 'destructive' })
    },
  })

  if (isLoading) {
    return (
      <div className="space-y-2 mt-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-8 w-full" />
        ))}
      </div>
    )
  }

  if (!factors?.length) {
    return (
      <p className="text-sm text-muted-foreground text-center py-4 mt-4">{td(t, 'predictions.noFactors')}</p>
    )
  }

  return (
    <div className="overflow-x-auto mt-4">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-muted-foreground">
            <th className="text-left py-2 font-medium">{td(t, 'predictions.factorName')}</th>
            <th className="text-left py-2 font-medium">{td(t, 'predictions.expression')}</th>
            <th className="text-left py-2 font-medium">{td(t, 'predictions.marketLabel')}</th>
            <th className="text-right py-2 font-medium">IC</th>
            <th className="text-right py-2 font-medium">ICIR</th>
            <th className="text-center py-2 font-medium">{td(t, 'predictions.active')}</th>
          </tr>
        </thead>
        <tbody>
          {factors.map((f: DiscoveredFactor) => (
            <tr key={f.id} className="border-b last:border-0 hover:bg-muted/50">
              <td className="py-2">
                <div>
                  <span className="font-medium text-xs">{f.name}</span>
                  {f.discoveryRound != null && (
                    <span className="text-xs text-muted-foreground ml-1">R{f.discoveryRound}</span>
                  )}
                </div>
                {f.description && (
                  <p className="text-xs text-muted-foreground truncate max-w-[200px]">{f.description}</p>
                )}
              </td>
              <td className="py-2 font-mono text-xs max-w-[200px] truncate">{f.expression}</td>
              <td className="py-2">{f.market.toUpperCase()}</td>
              <td className={cn(
                'py-2 text-right',
                f.ic != null && f.ic > 0.03 && 'text-green-600 dark:text-green-400',
              )}>
                {f.ic != null ? f.ic.toFixed(4) : '-'}
              </td>
              <td className="py-2 text-right">{f.icir != null ? f.icir.toFixed(4) : '-'}</td>
              <td className="py-2 text-center">
                <Button
                  size="sm"
                  variant={f.isActive ? 'default' : 'outline'}
                  className="h-6 px-2 text-xs"
                  disabled={toggleMutation.isPending}
                  onClick={() => toggleMutation.mutate({ id: f.id, isActive: !f.isActive })}
                >
                  {f.isActive ? td(t, 'predictions.on') : td(t, 'predictions.off')}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main Component ─────────────────────────────

export default function RDAgent() {
  const { t } = useTranslation('admin')

  return (
    <div className="space-y-4">
      {/* Market Cards */}
      <div className="grid gap-4 md:grid-cols-3">
        {MARKETS.map(mkt => (
          <MarketRDAgentCard key={mkt} market={mkt} />
        ))}
      </div>

      {/* Discovered Factors */}
      <div>
        <h3 className="text-sm font-medium mb-1">{td(t, 'predictions.discoveredFactors')}</h3>
        <p className="text-xs text-muted-foreground mb-2">{td(t, 'predictions.discoveredFactorsDesc')}</p>
        <DiscoveredFactorsTable />
      </div>
    </div>
  )
}
