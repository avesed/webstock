import { useState } from 'react'
import { BookOpen, ChevronRight, ExternalLink } from 'lucide-react'

import { cn } from '@/lib/utils'

interface RagSource {
  text?: string
  source_type?: string
  source_id?: string
  symbol?: string
  score?: number
  // Enriched news fields
  title?: string
  source?: string
  published_at?: string
  url?: string
  sentiment_tag?: string
  content_score?: number
  investment_summary?: string
  detailed_summary?: string
  industry_tags?: string[]
  event_tags?: string[]
}

interface RagSourcesBadgeProps {
  readonly sources: RagSource[]
}

const SENTIMENT_STYLES: Record<string, string> = {
  bullish: 'bg-green-500/10 text-green-500',
  bearish: 'bg-red-500/10 text-red-500',
  neutral: 'bg-blue-500/10 text-blue-400',
}

function formatRelativeTime(isoString: string): string {
  const diff = Date.now() - new Date(isoString).getTime()
  const hours = Math.floor(diff / 3_600_000)
  if (hours < 1) return '<1h'
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d`
  return `${Math.floor(days / 30)}mo`
}

export function RagSourcesBadge({ sources }: RagSourcesBadgeProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  if (sources.length === 0) return null

  const newsCount = sources.filter((s) => s.source_type === 'news' && s.title).length

  return (
    <div className="mx-11 mb-2">
      <button
        type="button"
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <BookOpen className="h-3 w-3" />
        <span>
          {sources.length} source{sources.length !== 1 ? 's' : ''} referenced
          {newsCount > 0 && ` (${newsCount} news)`}
        </span>
        <ChevronRight
          className={cn(
            'h-3 w-3 transition-transform',
            isExpanded && 'rotate-90'
          )}
        />
      </button>

      {isExpanded && (
        <div className="mt-1.5 space-y-1.5 rounded-md border bg-muted/30 p-2">
          {sources.map((source, index) => {
            const isNews = source.source_type === 'news' && source.title
            return isNews
              ? <NewsSourceItem key={index} index={index} source={source} />
              : <GenericSourceItem key={index} index={index} source={source} />
          })}
        </div>
      )}
    </div>
  )
}

function GenericSourceItem({ index, source }: { index: number; source: RagSource }) {
  const type = String(source.source_type ?? 'document')
  const symbol = source.symbol ?? null
  const title = source.title ?? null

  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-primary/10 text-[9px] font-medium text-primary">
        {index + 1}
      </span>
      <span className="capitalize font-medium">{type}</span>
      {symbol && (
        <span className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
          {symbol}
        </span>
      )}
      {title && <span className="truncate">{title}</span>}
    </div>
  )
}

function NewsSourceItem({ index, source }: { index: number; source: RagSource }) {
  const tags = [...(source.industry_tags ?? []), ...(source.event_tags ?? [])]

  return (
    <div className="rounded-md border bg-background/50 px-2.5 py-2 space-y-1">
      {/* Row 1: index + title + link */}
      <div className="flex items-start gap-2">
        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-primary/10 text-[9px] font-medium text-primary mt-0.5">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-foreground line-clamp-1">
              {source.title}
            </span>
            {source.url && (
              <a
                href={source.url}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 text-muted-foreground hover:text-foreground"
                onClick={(e) => e.stopPropagation()}
              >
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </div>

          {/* Row 2: meta badges */}
          <div className="flex flex-wrap items-center gap-1.5 mt-1">
            {source.symbol && (
              <span className="rounded bg-muted px-1 py-0.5 font-mono text-[10px] text-muted-foreground">
                {source.symbol}
              </span>
            )}
            {source.source && (
              <span className="text-[10px] text-muted-foreground">
                {source.source}
              </span>
            )}
            {source.published_at && (
              <span className="text-[10px] text-muted-foreground">
                {formatRelativeTime(source.published_at)}
              </span>
            )}
            {source.sentiment_tag && (
              <span
                className={cn(
                  'inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                  SENTIMENT_STYLES[source.sentiment_tag] ?? 'bg-muted text-muted-foreground'
                )}
              >
                {source.sentiment_tag}
              </span>
            )}
            {source.content_score != null && (
              <span className="text-[10px] text-muted-foreground">
                {source.content_score}/300
              </span>
            )}
            {tags.length > 0 && tags.slice(0, 3).map((tag) => (
              <span
                key={tag}
                className="rounded bg-accent/50 px-1 py-0.5 text-[10px] text-accent-foreground"
              >
                {tag}
              </span>
            ))}
          </div>

          {/* Row 3: investment summary */}
          {source.investment_summary && (
            <p className="text-[11px] text-muted-foreground mt-1 line-clamp-2">
              {source.investment_summary}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
