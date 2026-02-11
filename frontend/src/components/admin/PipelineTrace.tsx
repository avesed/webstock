import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { GitBranch, Search, CheckCircle, XCircle, MinusCircle, Clock, BarChart3 } from 'lucide-react'

import { adminApi, type PipelineStats, type ArticleTimeline } from '@/api/admin'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

const DAYS_OPTIONS = [1, 3, 7, 14, 30]

function formatMs(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 1) return '<1ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'success':
      return <CheckCircle className="h-4 w-4 text-green-500" />
    case 'error':
      return <XCircle className="h-4 w-4 text-red-500" />
    default:
      return <MinusCircle className="h-4 w-4 text-muted-foreground" />
  }
}

export default function PipelineTrace() {
  const { t } = useTranslation('admin')
  const [days, setDays] = useState(7)
  const [newsIdInput, setNewsIdInput] = useState('')
  const [searchNewsId, setSearchNewsId] = useState<string | null>(null)

  // Aggregate stats query
  const { data: stats, isLoading: statsLoading } = useQuery<PipelineStats>({
    queryKey: ['pipeline-stats', days],
    queryFn: () => adminApi.getPipelineStats(days),
    refetchInterval: 60000,
  })

  // Article timeline query (only when searchNewsId is set)
  const { data: timeline, isLoading: timelineLoading, error: timelineError } = useQuery<ArticleTimeline>({
    queryKey: ['pipeline-timeline', searchNewsId],
    queryFn: () => adminApi.getArticleTimeline(searchNewsId!),
    enabled: !!searchNewsId,
  })

  const handleSearch = () => {
    const trimmed = newsIdInput.trim()
    if (trimmed) {
      setSearchNewsId(trimmed)
    }
  }

  return (
    <div className="space-y-6">
      {/* Aggregate Stats Section */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <BarChart3 className="h-5 w-5" />
                {t('pipeline.statsTitle')}
              </CardTitle>
              <CardDescription>{t('pipeline.statsDescription')}</CardDescription>
            </div>
            <div className="flex gap-1">
              {DAYS_OPTIONS.map((d) => (
                <Button
                  key={d}
                  variant={days === d ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setDays(d)}
                >
                  {d}{t('filter.days')}
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {statsLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : stats && stats.nodes.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-2 pr-4">{t('pipeline.layer')}</th>
                    <th className="pb-2 pr-4">{t('pipeline.node')}</th>
                    <th className="pb-2 pr-4 text-right">{t('pipeline.total')}</th>
                    <th className="pb-2 pr-4 text-right">{t('pipeline.success')}</th>
                    <th className="pb-2 pr-4 text-right">{t('pipeline.errors')}</th>
                    <th className="pb-2 pr-4 text-right">{t('pipeline.avgMs')}</th>
                    <th className="pb-2 pr-4 text-right">P50</th>
                    <th className="pb-2 pr-4 text-right">P95</th>
                    <th className="pb-2 text-right">Max</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.nodes.map((node) => (
                    <tr
                      key={`${node.layer}-${node.node}`}
                      className={cn(
                        'border-b last:border-0',
                        node.errorCount > 0 && 'bg-red-50 dark:bg-red-950/20'
                      )}
                    >
                      <td className="py-2 pr-4">
                        <Badge variant="outline">L{node.layer}</Badge>
                      </td>
                      <td className="py-2 pr-4 font-mono text-xs">{node.node}</td>
                      <td className="py-2 pr-4 text-right">{node.count}</td>
                      <td className="py-2 pr-4 text-right text-green-600 dark:text-green-400">
                        {node.successCount}
                      </td>
                      <td className="py-2 pr-4 text-right text-red-600 dark:text-red-400">
                        {node.errorCount > 0 ? node.errorCount : '-'}
                      </td>
                      <td className="py-2 pr-4 text-right">{formatMs(node.avgMs)}</td>
                      <td className="py-2 pr-4 text-right">{formatMs(node.p50Ms)}</td>
                      <td className="py-2 pr-4 text-right">{formatMs(node.p95Ms)}</td>
                      <td className="py-2 text-right">{formatMs(node.maxMs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              <BarChart3 className="h-12 w-12 mx-auto mb-2 opacity-50" />
              <p>{t('pipeline.noData')}</p>
              <p className="text-xs mt-1">{t('pipeline.noDataHint')}</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Article Timeline Section */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            {t('pipeline.timelineTitle')}
          </CardTitle>
          <CardDescription>{t('pipeline.timelineDescription')}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-2 mb-4">
            <Input
              placeholder={t('pipeline.newsIdPlaceholder')}
              value={newsIdInput}
              onChange={(e) => setNewsIdInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="font-mono text-sm"
            />
            <Button onClick={handleSearch} disabled={!newsIdInput.trim()}>
              <Search className="h-4 w-4 mr-2" />
              {t('pipeline.search')}
            </Button>
          </div>

          {timelineLoading && (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          )}

          {timelineError && (
            <div className="text-center py-4 text-red-500">
              {t('pipeline.searchError')}
            </div>
          )}

          {timeline && (
            <div>
              {/* Article header */}
              {(timeline.title || timeline.symbol) && (
                <div className="mb-4 p-3 bg-muted/50 rounded-lg">
                  {timeline.symbol && (
                    <Badge variant="secondary" className="mr-2">{timeline.symbol}</Badge>
                  )}
                  {timeline.title && (
                    <span className="text-sm font-medium">{timeline.title}</span>
                  )}
                </div>
              )}

              {timeline.events.length === 0 ? (
                <div className="text-center py-4 text-muted-foreground">
                  {t('pipeline.noEvents')}
                </div>
              ) : (
                <div className="relative pl-6">
                  {/* Vertical line */}
                  <div className="absolute left-[11px] top-2 bottom-2 w-[2px] bg-border" />

                  {timeline.events.map((event) => (
                    <div key={event.id} className="relative mb-4 last:mb-0">
                      {/* Dot on timeline */}
                      <div className={cn(
                        'absolute -left-6 top-1 h-5 w-5 rounded-full border-2 flex items-center justify-center bg-background',
                        event.status === 'success' && 'border-green-500',
                        event.status === 'error' && 'border-red-500',
                        event.status === 'skip' && 'border-muted-foreground',
                      )}>
                        <StatusIcon status={event.status} />
                      </div>

                      {/* Event card */}
                      <div className={cn(
                        'p-3 rounded-lg border',
                        event.status === 'error' && 'border-red-200 dark:border-red-800 bg-red-50/50 dark:bg-red-950/20',
                        event.status === 'skip' && 'border-muted bg-muted/30',
                      )}>
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Badge variant="outline" className="text-xs">L{event.layer}</Badge>
                            <span className="font-mono text-sm font-medium">{event.node}</span>
                            <Badge
                              variant={event.status === 'success' ? 'default' : event.status === 'error' ? 'destructive' : 'secondary'}
                              className="text-xs"
                            >
                              {event.status}
                            </Badge>
                          </div>
                          {event.durationMs !== null && (
                            <span className="flex items-center gap-1 text-xs text-muted-foreground">
                              <Clock className="h-3 w-3" />
                              {formatMs(event.durationMs)}
                            </span>
                          )}
                        </div>

                        {/* Metadata */}
                        {event.metadata && Object.keys(event.metadata).length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {Object.entries(event.metadata).map(([key, value]) => (
                              <span key={key} className="text-xs bg-muted px-1.5 py-0.5 rounded">
                                {key}: {String(value)}
                              </span>
                            ))}
                          </div>
                        )}

                        {/* Error message */}
                        {event.error && (
                          <div className="mt-2 text-xs text-red-600 dark:text-red-400 font-mono bg-red-50 dark:bg-red-950/30 p-2 rounded">
                            {event.error}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Total duration */}
              {timeline.totalDurationMs !== null && (
                <div className="mt-4 pt-3 border-t text-sm text-muted-foreground flex items-center gap-2">
                  <Clock className="h-4 w-4" />
                  {t('pipeline.totalDuration')}: <span className="font-medium text-foreground">{formatMs(timeline.totalDurationMs)}</span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
