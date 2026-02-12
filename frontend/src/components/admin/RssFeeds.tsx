import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Plus, Trash2, RefreshCw, ExternalLink, AlertCircle, Check,
  ChevronDown, ChevronRight, Loader2, Play, Rss,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { getErrorMessage } from '@/api/client'
import { cn } from '@/lib/utils'
import type { RssFeed, RssFeedCreate, RssFeedUpdate, FeedCategory, RssFeedTestResult, RssFeedTestArticle } from '@/types'

// ─── Toggle switch (reused pattern from SystemSettings) ────────────────────

interface ToggleSwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}

function ToggleSwitch({ checked, onCheckedChange, disabled }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-input'
      )}
    >
      <span
        className={cn(
          'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-background shadow-lg ring-0 transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0'
        )}
      />
    </button>
  )
}

// ─── Form data interface ───────────────────────────────────────────────────

interface FeedFormData {
  name: string
  rsshubRoute: string
  description: string
  category: FeedCategory
  symbol: string
  market: string
  pollIntervalMinutes: number
  fulltextMode: boolean
}

const EMPTY_FORM: FeedFormData = {
  name: '',
  rsshubRoute: '',
  description: '',
  category: 'media',
  symbol: '',
  market: 'US',
  pollIntervalMinutes: 30,
  fulltextMode: false,
}

const CATEGORY_COLORS: Record<FeedCategory, string> = {
  media: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200',
  exchange: 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
  social: 'bg-purple-100 text-purple-800 dark:bg-purple-900 dark:text-purple-200',
}

const MARKETS = ['US', 'HK', 'SH', 'SZ', 'CN', 'METAL'] as const

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// ─── Status dot component ──────────────────────────────────────────────────

function StatusDot({ feed }: { feed: RssFeed }) {
  const { t } = useTranslation('admin')

  if (!feed.lastPolledAt) {
    return <span className="inline-block h-2.5 w-2.5 rounded-full bg-gray-400" title={t('rss.noPoll')} />
  }
  if (feed.lastError) {
    return <span className="inline-block h-2.5 w-2.5 rounded-full bg-yellow-500" title={feed.lastError} />
  }
  return <span className="inline-block h-2.5 w-2.5 rounded-full bg-green-500" title={t('rss.healthy')} />
}

// ─── Feed card for list ────────────────────────────────────────────────────

interface FeedCardProps {
  feed: RssFeed
  isSelected: boolean
  onSelect: () => void
  onToggle: () => void
  isPending: boolean
}

function FeedCard({ feed, isSelected, onSelect, onToggle, isPending }: FeedCardProps) {
  const { t } = useTranslation('admin')

  const categoryKey = `rss.category${capitalize(feed.category)}` as
    | 'rss.categoryMedia'
    | 'rss.categoryExchange'
    | 'rss.categorySocial'

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect() } }}
      className={cn(
        'p-3 rounded-lg border cursor-pointer transition-colors',
        isSelected
          ? 'border-primary bg-primary/5'
          : 'border-border hover:border-primary/50 hover:bg-muted/50'
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <StatusDot feed={feed} />
            <span className="font-medium text-sm truncate">{feed.name}</span>
          </div>
          <p className="text-xs font-mono text-muted-foreground mt-1 truncate">
            {feed.rsshubRoute}
          </p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <span className={cn(
              'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
              CATEGORY_COLORS[feed.category]
            )}>
              {t(categoryKey)}
            </span>
            <Badge variant="outline" className="text-xs py-0">
              {feed.market}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {feed.articleCount} {t('rss.articles')}
            </span>
            {feed.consecutiveErrors > 0 && (
              <span className="text-xs text-destructive font-medium">
                {feed.consecutiveErrors}x {t('rss.error')}
              </span>
            )}
          </div>
        </div>
        <div
          className="shrink-0"
          onClick={(e) => {
            e.stopPropagation()
          }}
        >
          <ToggleSwitch
            checked={feed.isEnabled}
            onCheckedChange={() => onToggle()}
            disabled={isPending}
          />
        </div>
      </div>
    </div>
  )
}

// ─── Test result display ───────────────────────────────────────────────────

function TestResultDisplay({ result }: { result: RssFeedTestResult }) {
  const { t } = useTranslation('admin')
  const [expanded, setExpanded] = useState(true)

  if (result.error) {
    return (
      <div className="rounded-md border border-destructive/50 bg-destructive/10 p-3">
        <div className="flex items-center gap-2 text-destructive text-sm">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{t('rss.testError')}: {result.error}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-md border p-3 space-y-2">
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-sm font-medium w-full text-left"
      >
        {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        <Check className="h-4 w-4 text-green-500" />
        <span>{t('rss.testArticles', { count: result.articleCount })}</span>
      </button>
      {expanded && result.articles.length > 0 && (
        <ul className="space-y-2 pl-5">
          {result.articles.map((article: RssFeedTestArticle, idx: number) => (
            <li key={idx} className="text-sm border-b border-border/50 pb-2 last:border-b-0 last:pb-0">
              <div className="flex items-start gap-1">
                <span className="font-medium flex-1">{article.title}</span>
                {article.url && (
                  <a
                    href={article.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="shrink-0 text-muted-foreground hover:text-foreground"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <ExternalLink className="h-3.5 w-3.5" />
                  </a>
                )}
              </div>
              {article.publishedAt && (
                <p className="text-xs text-muted-foreground mt-0.5">
                  {new Date(article.publishedAt).toLocaleString()}
                </p>
              )}
              {article.summary && (
                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                  {article.summary}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ─── Main component ────────────────────────────────────────────────────────

export function RssFeeds() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()

  // State: feed list
  const [feeds, setFeeds] = useState<RssFeed[]>([])
  const [loading, setLoading] = useState(true)

  // State: selection and form
  const [selectedFeedId, setSelectedFeedId] = useState<string | null>(null)
  const [isCreating, setIsCreating] = useState(false)
  const [formData, setFormData] = useState<FeedFormData>(EMPTY_FORM)

  // State: mutations
  const [saving, setSaving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [toggling, setToggling] = useState<string | null>(null)
  const [triggering, setTriggering] = useState(false)

  // State: test feed
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<RssFeedTestResult | null>(null)

  const selectedFeed = selectedFeedId
    ? feeds.find((f) => f.id === selectedFeedId) ?? null
    : null

  const isPending = saving || deleting || toggling !== null

  // ─── Data fetching ─────────────────────────────────────────────────────

  const fetchFeeds = useCallback(async () => {
    try {
      const data = await adminApi.listRssFeeds()
      setFeeds(data.feeds)
    } catch (error) {
      toast({ title: t('rss.fetchError'), description: getErrorMessage(error), variant: 'destructive' })
    } finally {
      setLoading(false)
    }
  }, [t, toast])

  useEffect(() => {
    void fetchFeeds()
  }, [fetchFeeds])

  // ─── Form helpers ──────────────────────────────────────────────────────

  const populateFormFromFeed = useCallback((feed: RssFeed) => {
    setFormData({
      name: feed.name,
      rsshubRoute: feed.rsshubRoute,
      description: feed.description ?? '',
      category: feed.category,
      symbol: feed.symbol ?? '',
      market: feed.market,
      pollIntervalMinutes: feed.pollIntervalMinutes,
      fulltextMode: feed.fulltextMode,
    })
    setTestResult(null)
  }, [])

  const handleSelectFeed = (id: string) => {
    setIsCreating(false)
    setSelectedFeedId(id)
    const feed = feeds.find((f) => f.id === id)
    if (feed) {
      populateFormFromFeed(feed)
    }
  }

  const handleCreateNew = () => {
    setSelectedFeedId(null)
    setIsCreating(true)
    setFormData(EMPTY_FORM)
    setTestResult(null)
  }

  const handleCancel = () => {
    if (isCreating) {
      setIsCreating(false)
      setFormData(EMPTY_FORM)
    } else if (selectedFeed) {
      populateFormFromFeed(selectedFeed)
    }
    setTestResult(null)
  }

  // ─── Mutations ─────────────────────────────────────────────────────────

  const handleSave = async () => {
    setSaving(true)
    try {
      if (isCreating) {
        const payload: RssFeedCreate = {
          name: formData.name,
          rsshubRoute: formData.rsshubRoute,
          description: formData.description || null,
          category: formData.category,
          symbol: formData.symbol || null,
          market: formData.market,
          pollIntervalMinutes: formData.pollIntervalMinutes,
          fulltextMode: formData.fulltextMode,
        }
        const newFeed = await adminApi.createRssFeed(payload)
        toast({ title: t('rss.created') })
        setIsCreating(false)
        await fetchFeeds()
        setSelectedFeedId(newFeed.id)
        populateFormFromFeed(newFeed)
      } else if (selectedFeedId) {
        const payload: RssFeedUpdate = {
          name: formData.name,
          rsshubRoute: formData.rsshubRoute,
          description: formData.description || null,
          category: formData.category,
          symbol: formData.symbol || null,
          market: formData.market,
          pollIntervalMinutes: formData.pollIntervalMinutes,
          fulltextMode: formData.fulltextMode,
        }
        const updated = await adminApi.updateRssFeed(selectedFeedId, payload)
        toast({ title: t('rss.updated') })
        await fetchFeeds()
        populateFormFromFeed(updated)
      }
    } catch (error) {
      toast({ title: t('rss.saveError'), description: getErrorMessage(error), variant: 'destructive' })
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!selectedFeedId) return
    const confirmed = window.confirm(t('rss.confirmDelete'))
    if (!confirmed) return

    setDeleting(true)
    try {
      await adminApi.deleteRssFeed(selectedFeedId)
      toast({ title: t('rss.deleted') })
      setSelectedFeedId(null)
      setFormData(EMPTY_FORM)
      setIsCreating(false)
      await fetchFeeds()
    } catch (error) {
      toast({ title: t('rss.deleteError'), description: getErrorMessage(error), variant: 'destructive' })
    } finally {
      setDeleting(false)
    }
  }

  const handleToggle = async (feedId: string) => {
    setToggling(feedId)
    try {
      await adminApi.toggleRssFeed(feedId)
      toast({ title: t('rss.toggled') })
      await fetchFeeds()
    } catch (error) {
      toast({ title: t('rss.toggleError'), description: getErrorMessage(error), variant: 'destructive' })
    } finally {
      setToggling(null)
    }
  }

  const handleTriggerMonitor = async () => {
    setTriggering(true)
    try {
      await adminApi.triggerRssMonitor()
      toast({ title: t('rss.triggerSuccess') })
    } catch (error) {
      toast({ title: t('rss.triggerError'), description: getErrorMessage(error), variant: 'destructive' })
    } finally {
      setTriggering(false)
    }
  }

  const handleTestFeed = async () => {
    if (!formData.rsshubRoute) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await adminApi.testRssFeed(formData.rsshubRoute, formData.fulltextMode)
      setTestResult(result)
    } catch (error) {
      const errorMsg = getErrorMessage(error)
      setTestResult({
        route: formData.rsshubRoute,
        articleCount: 0,
        articles: [],
        error: `${t('rss.testError')}: ${errorMsg}`,
      })
    } finally {
      setTesting(false)
    }
  }

  // ─── Form field updater ────────────────────────────────────────────────

  const updateField = <K extends keyof FeedFormData>(key: K, value: FeedFormData[K]) => {
    setFormData((prev) => ({ ...prev, [key]: value }))
  }

  const hasSelection = isCreating || selectedFeedId !== null

  // ─── Render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Header */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Rss className="h-5 w-5" />
                {t('rss.title')}
              </CardTitle>
              <CardDescription className="mt-1">{t('rss.description')}</CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => void handleTriggerMonitor()}
                disabled={triggering}
              >
                {triggering ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                {t('rss.triggerMonitor')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setLoading(true)
                  void fetchFeeds()
                }}
                disabled={loading}
              >
                <RefreshCw className={cn('h-4 w-4', loading && 'animate-spin')} />
              </Button>
            </div>
          </div>
        </CardHeader>
      </Card>

      {/* Two-panel layout */}
      <div className="flex gap-4" style={{ minHeight: '500px' }}>
        {/* Left panel: Feed list */}
        <div className="w-2/5 flex flex-col border rounded-lg">
          <div className="flex items-center justify-between p-3 border-b">
            <h3 className="font-medium text-sm">{t('rss.title')}</h3>
            <Button
              variant="outline"
              size="sm"
              onClick={handleCreateNew}
              disabled={isPending}
            >
              <Plus className="mr-1 h-4 w-4" />
              {t('rss.addFeed')}
            </Button>
          </div>

          <div className="flex-1 overflow-y-auto p-2 space-y-2">
            {loading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : feeds.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <Rss className="h-8 w-8 text-muted-foreground mb-2" />
                <p className="text-sm text-muted-foreground">{t('rss.noFeeds')}</p>
              </div>
            ) : (
              feeds.map((feed) => (
                <FeedCard
                  key={feed.id}
                  feed={feed}
                  isSelected={selectedFeedId === feed.id && !isCreating}
                  onSelect={() => handleSelectFeed(feed.id)}
                  onToggle={() => void handleToggle(feed.id)}
                  isPending={toggling === feed.id}
                />
              ))
            )}
          </div>
        </div>

        {/* Right panel: Detail / Form */}
        <div className="w-3/5 border rounded-lg overflow-y-auto">
          {!hasSelection ? (
            <div className="flex flex-col items-center justify-center h-full text-center p-8">
              <Rss className="h-10 w-10 text-muted-foreground mb-3" />
              <p className="text-sm text-muted-foreground">{t('rss.selectFeed')}</p>
            </div>
          ) : (
            <div className="p-4 space-y-4">
              <h3 className="font-semibold text-lg">
                {isCreating ? t('rss.addFeed') : t('rss.editFeed')}
              </h3>

              {/* Feed status info (only for existing feeds) */}
              {!isCreating && selectedFeed && (
                <div className="grid grid-cols-2 gap-3 p-3 rounded-md bg-muted/50">
                  <div>
                    <p className="text-xs text-muted-foreground">{t('rss.lastPolled')}</p>
                    <p className="text-sm font-medium">
                      {selectedFeed.lastPolledAt
                        ? new Date(selectedFeed.lastPolledAt).toLocaleString()
                        : t('rss.neverPolled')}
                    </p>
                  </div>
                  <div>
                    <p className="text-xs text-muted-foreground">{t('rss.articleCount')}</p>
                    <p className="text-sm font-medium">{selectedFeed.articleCount}</p>
                  </div>
                  {selectedFeed.lastError && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">{t('rss.lastError')}</p>
                      <p className="text-sm text-destructive">{selectedFeed.lastError}</p>
                    </div>
                  )}
                  {selectedFeed.consecutiveErrors > 0 && (
                    <div>
                      <p className="text-xs text-muted-foreground">{t('rss.consecutiveErrors')}</p>
                      <p className="text-sm font-medium text-destructive">
                        {selectedFeed.consecutiveErrors}
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* Form fields */}
              <div className="space-y-4">
                {/* Name */}
                <div className="space-y-2">
                  <Label htmlFor="feed-name">{t('rss.feedName')}</Label>
                  <Input
                    id="feed-name"
                    value={formData.name}
                    onChange={(e) => updateField('name', e.target.value)}
                    placeholder={t('rss.feedNamePlaceholder')}
                    disabled={isPending}
                  />
                </div>

                {/* RSSHub Route */}
                <div className="space-y-2">
                  <Label htmlFor="feed-route">{t('rss.route')}</Label>
                  <Input
                    id="feed-route"
                    value={formData.rsshubRoute}
                    onChange={(e) => updateField('rsshubRoute', e.target.value)}
                    placeholder={t('rss.routePlaceholder')}
                    disabled={isPending}
                    className="font-mono"
                  />
                  <p className="text-xs text-muted-foreground">{t('rss.routeHint')}</p>
                </div>

                {/* Description */}
                <div className="space-y-2">
                  <Label htmlFor="feed-description">{t('rss.feedDescription')}</Label>
                  <Textarea
                    id="feed-description"
                    value={formData.description}
                    onChange={(e) => updateField('description', e.target.value)}
                    disabled={isPending}
                    className="min-h-[60px]"
                  />
                </div>

                {/* Category + Market (side by side) */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="feed-category">{t('rss.category')}</Label>
                    <select
                      id="feed-category"
                      value={formData.category}
                      onChange={(e) => updateField('category', e.target.value as FeedCategory)}
                      disabled={isPending}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      <option value="media">{t('rss.categoryMedia')}</option>
                      <option value="exchange">{t('rss.categoryExchange')}</option>
                      <option value="social">{t('rss.categorySocial')}</option>
                    </select>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="feed-market">{t('rss.market')}</Label>
                    <select
                      id="feed-market"
                      value={formData.market}
                      onChange={(e) => updateField('market', e.target.value)}
                      disabled={isPending}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      {MARKETS.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Symbol + Poll Interval (side by side) */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="feed-symbol">{t('rss.symbol')}</Label>
                    <Input
                      id="feed-symbol"
                      value={formData.symbol}
                      onChange={(e) => updateField('symbol', e.target.value)}
                      disabled={isPending}
                    />
                    <p className="text-xs text-muted-foreground">{t('rss.symbolHint')}</p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="feed-interval">{t('rss.pollInterval')}</Label>
                    <Input
                      id="feed-interval"
                      type="number"
                      min={5}
                      max={1440}
                      value={formData.pollIntervalMinutes}
                      onChange={(e) => updateField('pollIntervalMinutes', parseInt(e.target.value, 10) || 30)}
                      disabled={isPending}
                    />
                  </div>
                </div>

                {/* Fulltext Mode */}
                <div className="flex items-center justify-between">
                  <div>
                    <Label>{t('rss.fulltextMode')}</Label>
                    <p className="text-xs text-muted-foreground">{t('rss.fulltextModeHint')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.fulltextMode}
                    onCheckedChange={(v) => updateField('fulltextMode', v)}
                    disabled={isPending}
                  />
                </div>

                {/* Test Feed */}
                <div className="space-y-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void handleTestFeed()}
                    disabled={testing || !formData.rsshubRoute}
                  >
                    {testing ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Play className="mr-2 h-4 w-4" />
                    )}
                    {testing ? t('rss.testing') : t('rss.testFeed')}
                  </Button>

                  {testResult && (
                    <TestResultDisplay result={testResult} />
                  )}
                </div>
              </div>

              {/* Action buttons */}
              <div className="flex items-center justify-between pt-2 border-t">
                <div>
                  {!isCreating && selectedFeedId && (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => void handleDelete()}
                      disabled={isPending}
                    >
                      {deleting ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="mr-2 h-4 w-4" />
                      )}
                      {t('rss.delete')}
                    </Button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleCancel}
                    disabled={isPending}
                  >
                    {t('rss.cancel')}
                  </Button>
                  <Button
                    size="sm"
                    onClick={() => void handleSave()}
                    disabled={isPending || !formData.name.trim() || !formData.rsshubRoute.trim()}
                  >
                    {saving ? (
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                      <Check className="mr-2 h-4 w-4" />
                    )}
                    {t('rss.save')}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
