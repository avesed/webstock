import { useState, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { DollarSign, Coins, Zap, Database, Calendar, Plus, Trash2, Loader2, MessageSquare, Brain, Newspaper } from 'lucide-react'

import { adminApi } from '@/api/admin'
import type { CostSummary, DailyCost, ModelPricingItem, CategoryBreakdownItem, LlmProvider } from '@/types'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
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
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useToast } from '@/hooks/useToast'
import { cn } from '@/lib/utils'

// ── Formatters ──────────────────────────────────

function formatCost(v: number): string {
  if (v === 0) return '$0.00'
  if (v < 0.01) return `$${v.toFixed(4)}`
  return `$${v.toFixed(2)}`
}

function formatTokens(v: number): string {
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return String(v)
}

// Dynamic i18n key helper — bypasses strict key checking for runtime-constructed keys
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const td = (t: (k: any) => string, key: string): string => t(key)

// ── Category mapping ────────────────────────────

type CategoryKey = 'general' | 'analysis' | 'news'

interface CategoryDef {
  key: CategoryKey
  labelKey: string
  icon: React.ReactNode
  purposes: string[]
}

const CATEGORIES: CategoryDef[] = [
  {
    key: 'general',
    labelKey: 'costs.categoryGeneral',
    icon: <MessageSquare className="h-4 w-4" />,
    purposes: ['chat', 'embedding', 'report'],
  },
  {
    key: 'analysis',
    labelKey: 'costs.categoryAnalysis',
    icon: <Brain className="h-4 w-4" />,
    purposes: ['analysis', 'synthesis', 'clarification'],
  },
  {
    key: 'news',
    labelKey: 'costs.categoryNews',
    icon: <Newspaper className="h-4 w-4" />,
    purposes: ['layer1_scoring', 'content_cleaning', 'layer3_analysis', 'layer3_lightweight', 'deep_filter'],
  },
]

const PURPOSE_LABEL_MAP: Record<string, string> = {
  chat: 'costs.purposeChat',
  embedding: 'costs.purposeEmbedding',
  report: 'costs.purposeReport',
  analysis: 'costs.purposeAnalysis',
  synthesis: 'costs.purposeSynthesis',
  clarification: 'costs.purposeClarification',
  layer1_scoring: 'costs.purposeLayer1',
  content_cleaning: 'costs.purposeContentCleaning',
  layer3_analysis: 'costs.purposeLayer3Full',
  layer3_lightweight: 'costs.purposeLayer3Lite',
  deep_filter: 'costs.purposeDeepFilter',
}

const SUBGROUP_LABEL_MAP: Record<string, string> = {
  fundamental: 'costs.agentFundamental',
  technical: 'costs.agentTechnical',
  sentiment: 'costs.agentSentiment',
  news: 'costs.agentNews',
  macro: 'costs.agentMacro',
  market: 'costs.agentMarket',
  signal: 'costs.agentSignal',
  entity_extractor: 'costs.agentEntityExtractor',
  sentiment_tags: 'costs.agentSentimentTags',
  summary_generator: 'costs.agentSummary',
  impact_assessor: 'costs.agentImpact',
  report_writer: 'costs.agentReport',
}

// ── StatCard ────────────────────────────────────

interface StatCardProps {
  title: string
  value: string
  subtitle?: string
  icon?: React.ReactNode
  className?: string
}

function StatCard({ title, value, subtitle, icon, className }: StatCardProps) {
  return (
    <Card className={cn('', className)}>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {subtitle && (
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        )}
      </CardContent>
    </Card>
  )
}

// ── Category Breakdown Cards ────────────────────

function CategoryCards({
  data,
}: {
  data: CategoryBreakdownItem[]
}) {
  const { t } = useTranslation('admin')

  // Group data by category
  const grouped = useMemo(() => {
    const result: Record<CategoryKey, CategoryBreakdownItem[]> = {
      general: [],
      analysis: [],
      news: [],
    }
    for (const item of data) {
      const cat = CATEGORIES.find((c) => c.purposes.includes(item.purpose))
      if (cat) {
        result[cat.key].push(item)
      }
    }
    return result
  }, [data])

  if (data.length === 0) {
    return null
  }

  return (
    <div className="space-y-4">
      {CATEGORIES.map((cat) => {
        const items = grouped[cat.key]
        if (items.length === 0) return null

        const totalCost = items.reduce((s, i) => s + i.costUsd, 0)
        const totalTokens = items.reduce((s, i) => s + i.totalTokens, 0)
        const totalCalls = items.reduce((s, i) => s + i.calls, 0)

        // Group by purpose then show sub-groups
        const purposeMap = new Map<string, CategoryBreakdownItem[]>()
        for (const item of items) {
          const existing = purposeMap.get(item.purpose) ?? []
          existing.push(item)
          purposeMap.set(item.purpose, existing)
        }

        return (
          <Card key={cat.key}>
            <CardHeader className="pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base flex items-center gap-2">
                  {cat.icon}
                  {td(t, cat.labelKey)}
                </CardTitle>
                <div className="flex items-center gap-3 text-sm text-muted-foreground">
                  <span>{formatCost(totalCost)}</span>
                  <Badge variant="secondary" className="font-normal">
                    {formatTokens(totalTokens)} tokens
                  </Badge>
                  <Badge variant="outline" className="font-normal">
                    {totalCalls} {t('costs.calls').toLowerCase()}
                  </Badge>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-3">
              {[...purposeMap.entries()].map(([purpose, purposeItems]) => {
                const purposeCost = purposeItems.reduce((s, i) => s + i.costUsd, 0)
                const hasSubGroups = purposeItems.some((i) => i.subGroup !== '')
                const maxCost = Math.max(totalCost, 0.0001)

                return (
                  <div key={purpose} className="space-y-1.5">
                    {/* Purpose row */}
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-medium">
                        {td(t, PURPOSE_LABEL_MAP[purpose] ?? purpose)}
                      </span>
                      <div className="flex items-center gap-3 text-muted-foreground shrink-0">
                        <span className="text-xs">
                          {formatTokens(purposeItems.reduce((s, i) => s + i.promptTokens, 0))} {t('costs.inputTokens').toLowerCase()}
                          {' / '}
                          {formatTokens(purposeItems.reduce((s, i) => s + i.completionTokens, 0))} {t('costs.outputTokens').toLowerCase()}
                        </span>
                        <span className="font-medium text-foreground">{formatCost(purposeCost)}</span>
                      </div>
                    </div>
                    <div className="relative h-2 rounded-full overflow-hidden bg-muted">
                      <div
                        className="absolute inset-y-0 left-0 rounded-full bg-primary/80 transition-all"
                        style={{ width: `${(purposeCost / maxCost) * 100}%` }}
                      />
                    </div>

                    {/* Sub-groups (if any) */}
                    {hasSubGroups && (
                      <div className="ml-4 space-y-1 pt-1">
                        {purposeItems
                          .filter((i) => i.subGroup !== '')
                          .map((item) => {
                            const subLabel = td(t, SUBGROUP_LABEL_MAP[item.subGroup] ?? item.subGroup)
                            return (
                              <div key={`${purpose}-${item.subGroup}`} className="flex items-center justify-between text-xs text-muted-foreground">
                                <span className="flex items-center gap-1.5">
                                  <span className="h-1.5 w-1.5 rounded-full bg-primary/60 inline-block shrink-0" />
                                  {subLabel}
                                </span>
                                <div className="flex items-center gap-3">
                                  <span>{formatTokens(item.totalTokens)} tok</span>
                                  <span>{item.calls} calls</span>
                                  <span className="font-medium text-foreground/80">{formatCost(item.costUsd)}</span>
                                </div>
                              </div>
                            )
                          })}
                      </div>
                    )}
                  </div>
                )
              })}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

// ── Daily Cost Chart ────────────────────────────

function DailyCostChart({ data }: { data: DailyCost[] }) {
  const { t } = useTranslation('admin')

  if (data.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t('costs.dailyCosts')}</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground text-center py-4">
            {t('costs.noUsageData')}
          </p>
        </CardContent>
      </Card>
    )
  }

  const maxCost = Math.max(...data.map((d) => d.costUsd), 0.0001)
  const chartHeight = 140
  const barGap = 2
  const barWidth = Math.max(4, Math.min(20, (600 - data.length * barGap) / data.length))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{t('costs.dailyCosts')}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <svg
            role="img"
            aria-label={t('costs.dailyCosts')}
            width={Math.max(data.length * (barWidth + barGap) + 40, 300)}
            height={chartHeight + 40}
            className="mx-auto"
          >
            {data.map((day, i) => {
              const barHeight = maxCost > 0
                ? (day.costUsd / maxCost) * chartHeight
                : 0
              const x = i * (barWidth + barGap) + 20
              const y = chartHeight - barHeight
              return (
                <g key={day.date}>
                  <rect
                    x={x}
                    y={y}
                    width={barWidth}
                    height={Math.max(barHeight, 1)}
                    rx={2}
                    className="fill-primary/80 hover:fill-primary transition-colors"
                  >
                    <title>{`${day.date}: ${formatCost(day.costUsd)} (${formatTokens(day.promptTokens + day.completionTokens)} tokens, ${day.calls} calls)`}</title>
                  </rect>
                  {(i % Math.max(1, Math.floor(data.length / 10)) === 0 || i === data.length - 1) && (
                    <text
                      x={x + barWidth / 2}
                      y={chartHeight + 16}
                      textAnchor="middle"
                      className="fill-muted-foreground text-[9px]"
                    >
                      {day.date.slice(5)}
                    </text>
                  )}
                </g>
              )
            })}
            <text x={0} y={12} className="fill-muted-foreground text-[10px]">
              {formatCost(maxCost)}
            </text>
            <line
              x1={18}
              y1={chartHeight}
              x2={data.length * (barWidth + barGap) + 22}
              y2={chartHeight}
              className="stroke-border"
              strokeWidth={1}
            />
          </svg>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-4 mt-2 text-xs text-muted-foreground">
          <span>{t('costs.date')}: {data[0]?.date} - {data[data.length - 1]?.date}</span>
          <span>
            {t('costs.totalCost')}: {formatCost(data.reduce((sum, d) => sum + d.costUsd, 0))}
          </span>
        </div>
      </CardContent>
    </Card>
  )
}

// ── Pricing Section ─────────────────────────────

function PricingSection() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const [showAddDialog, setShowAddDialog] = useState(false)
  const [showDeleteDialog, setShowDeleteDialog] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formData, setFormData] = useState({
    model: '',
    inputPrice: '',
    cachedInputPrice: '',
    outputPrice: '',
    effectiveFrom: '',
  })

  const { data: pricing, isLoading } = useQuery<ModelPricingItem[]>({
    queryKey: ['admin', 'model-pricing'],
    queryFn: () => adminApi.getModelPricing(),
  })

  // Fetch configured LLM providers for model selector
  const { data: providers } = useQuery<LlmProvider[]>({
    queryKey: ['admin', 'llm-providers'],
    queryFn: () => adminApi.listLlmProviders(),
    enabled: showAddDialog,
  })

  // Deduplicate models across providers, preserving provider grouping
  const providerModels = useMemo(() => {
    if (!providers) return []
    const seen = new Set<string>()
    return providers
      .filter((p) => p.isEnabled && p.models.length > 0)
      .map((p) => ({
        providerName: p.name,
        models: p.models.filter((m) => {
          if (seen.has(m)) return false
          seen.add(m)
          return true
        }),
      }))
      .filter((g) => g.models.length > 0)
  }, [providers])

  const handleSubmit = async () => {
    if (!formData.model || !formData.inputPrice || !formData.outputPrice) return

    setIsSubmitting(true)
    try {
      const payload = {
        model: formData.model,
        inputPrice: parseFloat(formData.inputPrice),
        cachedInputPrice: formData.cachedInputPrice ? parseFloat(formData.cachedInputPrice) : null,
        outputPrice: parseFloat(formData.outputPrice),
      } as Parameters<typeof adminApi.createModelPricing>[0]

      if (formData.effectiveFrom) {
        payload.effectiveFrom = formData.effectiveFrom
      }

      await adminApi.createModelPricing(payload)
      toast({ title: t('costs.pricingSaved') })
      setShowAddDialog(false)
      setFormData({ model: '', inputPrice: '', cachedInputPrice: '', outputPrice: '', effectiveFrom: '' })
      queryClient.invalidateQueries({ queryKey: ['admin', 'model-pricing'] })
    } catch {
      toast({ title: t('costs.error'), description: t('costs.saveFailed'), variant: 'destructive' })
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await adminApi.deleteModelPricing(id)
      toast({ title: t('costs.pricingDeleted') })
      setShowDeleteDialog(null)
      queryClient.invalidateQueries({ queryKey: ['admin', 'model-pricing'] })
    } catch {
      toast({ title: t('costs.error'), description: t('costs.deleteFailed'), variant: 'destructive' })
    }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-base">{t('costs.pricing')}</CardTitle>
              <CardDescription>{t('costs.pricingDescription')}</CardDescription>
            </div>
            <Button size="sm" onClick={() => setShowAddDialog(true)}>
              <Plus className="h-4 w-4 mr-1" />
              {t('costs.addPricing')}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {[...Array(3)].map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !pricing?.length ? (
            <p className="text-sm text-muted-foreground text-center py-4">
              {t('costs.noPricingData')}
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-muted-foreground">
                    <th className="text-left py-2 font-medium">{t('costs.model')}</th>
                    <th className="text-right py-2 font-medium">{t('costs.inputPrice')}</th>
                    <th className="text-right py-2 font-medium">{t('costs.cachedInputPrice')}</th>
                    <th className="text-right py-2 font-medium">{t('costs.outputPrice')}</th>
                    <th className="text-right py-2 font-medium">{t('costs.effectiveFrom')}</th>
                    <th className="text-right py-2 font-medium w-12"></th>
                  </tr>
                </thead>
                <tbody>
                  {pricing.map((item) => (
                    <tr key={item.id} className="border-b last:border-0">
                      <td className="py-2 font-mono text-xs">{item.model}</td>
                      <td className="text-right py-2 font-mono">${item.inputPrice}</td>
                      <td className="text-right py-2 font-mono">
                        {item.cachedInputPrice != null ? `$${item.cachedInputPrice}` : '-'}
                      </td>
                      <td className="text-right py-2 font-mono">${item.outputPrice}</td>
                      <td className="text-right py-2 text-muted-foreground">
                        {item.effectiveFrom ? new Date(item.effectiveFrom).toLocaleDateString() : '-'}
                      </td>
                      <td className="text-right py-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                          onClick={() => setShowDeleteDialog(item.id)}
                          aria-label={`${t('costs.delete')} ${item.model}`}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Add Pricing Dialog */}
      <Dialog open={showAddDialog} onOpenChange={setShowAddDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('costs.addPricing')}</DialogTitle>
            <DialogDescription>
              {t('costs.pricingDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>{t('costs.model')}</Label>
              {providerModels.length > 0 ? (
                <Select
                  value={formData.model}
                  onValueChange={(val) => setFormData({ ...formData, model: val })}
                >
                  <SelectTrigger>
                    <SelectValue placeholder={t('costs.selectModel')} />
                  </SelectTrigger>
                  <SelectContent>
                    {providerModels.map((group) => (
                      <SelectGroup key={group.providerName}>
                        <SelectLabel>{group.providerName}</SelectLabel>
                        {group.models.map((model) => (
                          <SelectItem key={model} value={model}>
                            {model}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    ))}
                  </SelectContent>
                </Select>
              ) : (
                <p className="text-sm text-muted-foreground py-2">
                  {t('costs.noModelsAvailable')}
                </p>
              )}
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="space-y-2">
                <Label>{t('costs.inputPrice')}</Label>
                <Input
                  type="number"
                  step="0.001"
                  min="0"
                  placeholder="0.15"
                  value={formData.inputPrice}
                  onChange={(e) => setFormData({ ...formData, inputPrice: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('costs.cachedInputPrice')}</Label>
                <Input
                  type="number"
                  step="0.001"
                  min="0"
                  placeholder="0.075"
                  value={formData.cachedInputPrice}
                  onChange={(e) => setFormData({ ...formData, cachedInputPrice: e.target.value })}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('costs.outputPrice')}</Label>
                <Input
                  type="number"
                  step="0.001"
                  min="0"
                  placeholder="0.60"
                  value={formData.outputPrice}
                  onChange={(e) => setFormData({ ...formData, outputPrice: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label>{t('costs.effectiveFrom')}</Label>
              <Input
                type="date"
                value={formData.effectiveFrom}
                onChange={(e) => setFormData({ ...formData, effectiveFrom: e.target.value })}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowAddDialog(false)}>
              {t('costs.cancel')}
            </Button>
            <Button
              onClick={handleSubmit}
              disabled={isSubmitting || !formData.model || !formData.inputPrice || !formData.outputPrice}
            >
              {isSubmitting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              {t('costs.addPricing')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteDialog !== null} onOpenChange={() => setShowDeleteDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('costs.deletePricing')}</DialogTitle>
            <DialogDescription>
              {t('costs.deletePricingConfirm')}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowDeleteDialog(null)}>
              {t('costs.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => showDeleteDialog && handleDelete(showDeleteDialog)}
            >
              <Trash2 className="h-4 w-4 mr-1" />
              {t('costs.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

// ── Main Component ──────────────────────────────

export default function CostTracking() {
  const { t } = useTranslation('admin')
  const [periodDays, setPeriodDays] = useState(7)
  const [customRange, setCustomRange] = useState(false)
  const [startDate, setStartDate] = useState('')
  const [endDate, setEndDate] = useState('')

  // Effective query params: custom range overrides preset days
  const queryStartDate = customRange && startDate ? startDate : undefined
  const queryEndDate = customRange && endDate ? endDate : undefined
  const queryDays = customRange && startDate ? 365 : periodDays  // large fallback when using date range

  const periodOptions = [
    { days: 7, label: t('costs.days7') },
    { days: 30, label: t('costs.days30') },
    { days: 90, label: t('costs.days90') },
  ] as const

  const { data: summary, isLoading: summaryLoading } = useQuery<CostSummary>({
    queryKey: ['admin', 'cost-summary', queryDays, queryStartDate, queryEndDate],
    queryFn: () => adminApi.getCostSummary(queryDays, queryStartDate, queryEndDate),
    refetchInterval: 60000,
  })

  const { data: dailyCosts, isLoading: dailyLoading } = useQuery<DailyCost[]>({
    queryKey: ['admin', 'daily-costs', queryDays, queryStartDate, queryEndDate],
    queryFn: () => adminApi.getDailyCosts(queryDays, undefined, undefined, queryStartDate, queryEndDate),
    refetchInterval: 60000,
  })

  const { data: categoryData } = useQuery<CategoryBreakdownItem[]>({
    queryKey: ['admin', 'category-breakdown', queryDays, queryStartDate, queryEndDate],
    queryFn: () => adminApi.getCategoryBreakdown(queryDays, queryStartDate, queryEndDate),
    refetchInterval: 60000,
  })

  const cacheRate = summary && summary.totalTokens > 0
    ? ((summary.totalCachedTokens / (summary.totalPromptTokens + summary.totalCachedTokens)) * 100)
    : 0

  const handlePresetClick = (days: number) => {
    setPeriodDays(days)
    setCustomRange(false)
    setStartDate('')
    setEndDate('')
  }

  const handleCustomToggle = () => {
    setCustomRange(!customRange)
  }

  if (summaryLoading) {
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

  return (
    <div className="space-y-6">
      {/* Period Selector with Custom Date Range */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <DollarSign className="h-5 w-5 text-muted-foreground" />
                <h2 className="text-lg font-semibold">{t('costs.title')}</h2>
              </div>
              <div role="group" aria-label={t('costs.date')} className="flex items-center gap-1 rounded-lg border bg-muted/40 p-0.5">
                <Calendar className="h-3.5 w-3.5 ml-2 text-muted-foreground" />
                {periodOptions.map((opt) => (
                  <button
                    key={opt.days}
                    onClick={() => handlePresetClick(opt.days)}
                    aria-label={opt.label}
                    aria-pressed={!customRange && periodDays === opt.days}
                    className={cn(
                      'px-2.5 py-1 text-xs font-medium rounded-md transition-colors',
                      !customRange && periodDays === opt.days
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
                <div className="w-px h-4 bg-border mx-0.5" />
                <button
                  onClick={handleCustomToggle}
                  aria-pressed={customRange}
                  className={cn(
                    'px-2.5 py-1 text-xs font-medium rounded-md transition-colors',
                    customRange
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {t('costs.customRange')}
                </button>
              </div>
            </div>

            {/* Custom date inputs */}
            {customRange && (
              <div className="flex items-center gap-2 justify-end">
                <Label className="text-xs text-muted-foreground">{t('costs.startDate')}</Label>
                <Input
                  type="date"
                  className="w-36 h-8 text-xs"
                  value={startDate}
                  onChange={(e) => setStartDate(e.target.value)}
                />
                <span className="text-muted-foreground text-xs">—</span>
                <Label className="text-xs text-muted-foreground">{t('costs.endDate')}</Label>
                <Input
                  type="date"
                  className="w-36 h-8 text-xs"
                  value={endDate}
                  onChange={(e) => setEndDate(e.target.value)}
                />
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Summary Cards */}
      {!summary ? (
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground text-center">{t('costs.noUsageData')}</p>
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
            <StatCard
              title={t('costs.totalCost')}
              value={formatCost(summary.totalCostUsd)}
              subtitle={customRange && startDate
                ? `${startDate} — ${endDate || t('costs.endDate')}`
                : `${summary.periodDays} ${t('filter.days')}`}
              icon={<DollarSign className="h-4 w-4 text-muted-foreground" />}
            />
            <StatCard
              title={t('costs.totalTokens')}
              value={formatTokens(summary.totalTokens)}
              subtitle={t('costs.inOut', { input: formatTokens(summary.totalPromptTokens), output: formatTokens(summary.totalCompletionTokens) })}
              icon={<Coins className="h-4 w-4 text-muted-foreground" />}
            />
            <StatCard
              title={t('costs.totalCalls')}
              value={summary.totalCalls.toLocaleString()}
              subtitle={t('costs.perDay', { value: (summary.totalCalls / Math.max(summary.periodDays, 1)).toFixed(0) })}
              icon={<Zap className="h-4 w-4 text-muted-foreground" />}
            />
            <StatCard
              title={t('costs.cacheSavings')}
              value={summary.totalCachedTokens > 0 ? `${cacheRate.toFixed(1)}%` : '-'}
              subtitle={summary.totalCachedTokens > 0 ? t('costs.cachedCount', { value: formatTokens(summary.totalCachedTokens) }) : t('costs.noCacheData')}
              icon={<Database className="h-4 w-4 text-muted-foreground" />}
            />
          </div>

          {/* Category Breakdown Cards */}
          <CategoryCards data={categoryData ?? []} />

          {/* Daily Cost Chart */}
          {dailyLoading ? (
            <Card>
              <CardHeader>
                <Skeleton className="h-5 w-32" />
              </CardHeader>
              <CardContent>
                <Skeleton className="h-[180px] w-full" />
              </CardContent>
            </Card>
          ) : (
            <DailyCostChart data={dailyCosts ?? []} />
          )}
        </>
      )}

      {/* Model Pricing Section */}
      <Separator />
      <PricingSection />
    </div>
  )
}
