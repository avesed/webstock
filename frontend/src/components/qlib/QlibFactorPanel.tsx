import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { Loader2, AlertCircle, BarChart3 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { qlibApi } from '@/api/qlib'
import type { QlibTopFactor } from '@/api/qlib'

interface QlibFactorPanelProps {
  symbol: string
  market: string
}

/**
 * Classify a z-score into a color category.
 * - Green for strongly positive (> 0.5)
 * - Red for strongly negative (< -0.5)
 * - Neutral (default text color) otherwise
 */
function getZScoreColor(zScore: number): string {
  if (zScore > 0.5) return 'text-stock-up'
  if (zScore < -0.5) return 'text-stock-down'
  return 'text-muted-foreground'
}

/**
 * Format a factor value for display.
 * Values with large magnitudes use compact notation;
 * small values show up to 4 decimal places.
 */
function formatFactorValue(value: number): string {
  if (Math.abs(value) >= 1_000_000) {
    return value.toLocaleString(undefined, {
      notation: 'compact',
      maximumFractionDigits: 2,
    })
  }
  if (Math.abs(value) < 0.001 && value !== 0) {
    return value.toExponential(2)
  }
  return value.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 4,
  })
}

function FactorTable({ factors }: { factors: QlibTopFactor[] }) {
  const { t } = useTranslation('common')

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-left">
            <th className="pb-2 pr-4 font-medium text-muted-foreground">
              {t('qlib.factorName', 'Factor Name')}
            </th>
            <th className="pb-2 pr-4 text-right font-medium text-muted-foreground">
              {t('qlib.value', 'Value')}
            </th>
            <th className="pb-2 text-right font-medium text-muted-foreground">
              {t('qlib.zScore', 'Z-Score')}
            </th>
          </tr>
        </thead>
        <tbody>
          {factors.map((factor) => (
            <tr
              key={factor.name}
              className="border-b border-border/50 last:border-0"
            >
              <td className="py-2 pr-4 font-mono text-xs">
                {factor.name}
              </td>
              <td className="py-2 pr-4 text-right tabular-nums">
                {formatFactorValue(factor.value)}
              </td>
              <td
                className={cn(
                  'py-2 text-right font-medium tabular-nums',
                  getZScoreColor(factor.zScore),
                )}
              >
                {factor.zScore >= 0 ? '+' : ''}
                {factor.zScore.toFixed(2)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function QlibFactorPanel({ symbol, market }: QlibFactorPanelProps) {
  const { t } = useTranslation('common')

  // First check if the qlib service is available
  const {
    data: status,
    isLoading: isLoadingStatus,
    error: statusError,
  } = useQuery({
    queryKey: ['qlib-status'],
    queryFn: qlibApi.getStatus,
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: 1,
  })

  // Fetch factor data only when the service is available
  const {
    data: factorData,
    isLoading: isLoadingFactors,
    error: factorError,
  } = useQuery({
    queryKey: ['qlib-factors', symbol, market],
    queryFn: () => qlibApi.getFactors(symbol, market.toLowerCase()),
    enabled: !!symbol && status?.available === true,
    staleTime: 2 * 60 * 1000, // 2 minutes
    retry: 1,
  })

  const isLoading = isLoadingStatus || isLoadingFactors

  // Service unavailable state
  if (!isLoading && (statusError || status?.available === false)) {
    const errorMessage = status?.error ?? (statusError instanceof Error ? statusError.message : undefined)
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('qlib.factors', 'Quantitative Factors')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-10 w-10 text-muted-foreground" />
            <div>
              <p className="font-medium text-muted-foreground">
                {t('qlib.serviceUnavailable', 'Qlib service is not available')}
              </p>
              {errorMessage && (
                <p className="mt-1 text-sm text-muted-foreground/70">
                  {errorMessage}
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Loading state
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('qlib.factors', 'Quantitative Factors')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-[200px] items-center justify-center gap-2">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="text-muted-foreground">
              {t('qlib.factorsLoading', 'Loading factor data...')}
            </span>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Factor fetch error
  if (factorError) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('qlib.factors', 'Quantitative Factors')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
            <AlertCircle className="h-10 w-10 text-destructive" />
            <p className="font-medium text-muted-foreground">
              {t('qlib.factorsError', 'Failed to load factor data')}
            </p>
          </div>
        </CardContent>
      </Card>
    )
  }

  // No data state
  if (!factorData || factorData.topFactors.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('qlib.factors', 'Quantitative Factors')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex h-[200px] items-center justify-center text-muted-foreground">
            {t('qlib.noData', 'No factor data available for this symbol')}
          </div>
        </CardContent>
      </Card>
    )
  }

  // Determine the latest date for display
  const latestDate = factorData.dates.length > 0
    ? factorData.dates[factorData.dates.length - 1]
    : null

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5" />
              {t('qlib.factors', 'Quantitative Factors')}
            </CardTitle>
            <CardDescription className="mt-1.5">
              {t('qlib.factorsDescription', '{{type}} factor set - {{total}} factors computed', {
                type: factorData.alphaType,
                total: String(factorData.factorCount),
              })}
            </CardDescription>
          </div>
          <div className="text-right text-sm text-muted-foreground">
            {latestDate && (
              <div>
                {t('qlib.latestDate', 'Latest: {{date}}', { date: latestDate })}
              </div>
            )}
            <div className="mt-0.5">
              {t('qlib.mode', 'Mode: {{mode}}', { mode: factorData.mode })}
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="mb-3">
          <h4 className="text-sm font-medium text-muted-foreground">
            {t('qlib.topFactors', 'Top Factors by Absolute Z-Score')}
          </h4>
        </div>
        <FactorTable factors={factorData.topFactors} />
      </CardContent>
    </Card>
  )
}
