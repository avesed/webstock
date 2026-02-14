import { useCallback } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Clock,
  TrendingUp,
  TrendingDown,
  Minus,
  Zap,
  Ban,
  AlertTriangle,
  X,
  ExternalLink,
  Loader2,
} from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn, formatRelativeTime } from '@/lib/utils'
import type { NewsArticle, NewsSentiment, NewsNavigationContext } from '@/types'

const HTML_ENTITY_MAP: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&apos;': "'",
}

function decodeEntities(text: string): string {
  return text.replace(
    /&(?:#x([0-9a-fA-F]+)|#(\d+)|amp|lt|gt|quot|apos);/g,
    (match, hex, dec) => {
      if (hex != null) return String.fromCodePoint(parseInt(hex, 16))
      if (dec != null) return String.fromCodePoint(parseInt(dec, 10))
      return HTML_ENTITY_MAP[match] ?? match
    }
  )
}

interface NewsCardProps {
  article: NewsArticle
  compact?: boolean
  className?: string
  onSymbolClick?: (symbol: string) => void
  navigationContext?: NewsNavigationContext
}

const SENTIMENT_CONFIG: Record<NewsSentiment, { icon: typeof TrendingUp; color: string; translationKey: string }> = {
  POSITIVE: { icon: TrendingUp, color: 'text-stock-up', translationKey: 'news.positive' },
  NEGATIVE: { icon: TrendingDown, color: 'text-stock-down', translationKey: 'news.negative' },
  NEUTRAL: { icon: Minus, color: 'text-blue-400', translationKey: 'news.neutral' },
}

function StatusBadge({ article }: { article: NewsArticle }) {
  const { t } = useTranslation('dashboard')
  const score = article.contentScore
  const path = article.processingPath
  const cs = article.contentStatus
  const fs = article.filterStatus
  const details = article.scoreDetails
  const dims = details?.dimensionScores
  const isCritical = details?.isCriticalEvent

  if (!cs && !fs && score == null && !path) return null

  let text: string
  let label: string | undefined
  let color: string
  let Icon: typeof Zap | undefined

  if (path === 'error') {
    text = t('news.statusScoringError', 'Score Error')
    color = 'bg-orange-500/10 text-orange-400 border-orange-500/20'
    Icon = AlertTriangle
  } else if (score != null && path) {
    text = String(score)
    label = path === 'full_analysis'
      ? t('news.pathFull', 'Full')
      : t('news.pathLite', 'Lite')
    color = isCritical
      ? 'bg-red-500/15 text-red-500 border-red-500/30'
      : score >= 195
        ? 'bg-green-500/15 text-green-500 border-green-500/30'
        : score >= 105
          ? 'bg-yellow-500/15 text-yellow-500 border-yellow-500/30'
          : 'bg-muted text-muted-foreground border-border'
    Icon = isCritical ? Zap : undefined
  } else if (cs === 'deleted' || fs === 'delete') {
    text = t('news.statusDeleted', 'Filtered')
    color = 'bg-muted/50 text-muted-foreground border-border'
    Icon = X
  } else if (cs === 'failed') {
    text = t('news.statusFailed', 'Failed')
    color = 'bg-red-500/10 text-red-400 border-red-500/20'
    Icon = AlertTriangle
  } else if (cs === 'blocked') {
    text = t('news.statusBlocked', 'Blocked')
    color = 'bg-orange-500/10 text-orange-400 border-orange-500/20'
    Icon = Ban
  } else if (cs === 'embedded' || cs === 'fetched' || cs === 'partial') {
    return null
  } else {
    text = t('news.statusPending', 'Pending')
    color = 'bg-muted/50 text-muted-foreground border-border'
    Icon = Clock
  }

  const hasTooltip = !!dims

  const inner = (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold tabular-nums cursor-default',
        color
      )}
    >
      {Icon && <Icon className="h-2.5 w-2.5" />}
      {text}
      {label && <span className="text-[9px] font-normal opacity-70">{label}</span>}
    </span>
  )

  if (!hasTooltip) return inner

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>{inner}</TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-xs">
          <div className="space-y-1.5 text-xs">
            {path === 'error' ? (
              <div className="font-medium text-orange-400">
                {t('news.statusScoringError', 'Score Error')}
              </div>
            ) : (
              <div className="font-medium">
                {t('news.scoreBreakdown', 'Score Breakdown')} — {score ?? 0}/300
                {isCritical && (
                  <span className="ml-1 text-red-400">{t('news.criticalEvent', 'Critical')}</span>
                )}
              </div>
            )}
            {dims && Object.keys(dims).length > 0 && (
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span className="text-muted-foreground">{t('news.dimMacro', 'Macro')}</span>
                <span className="font-medium">{dims.macro ?? '-'}/100</span>
                <span className="text-muted-foreground">{t('news.dimMarket', 'Market')}</span>
                <span className="font-medium">{dims.market ?? '-'}/100</span>
                <span className="text-muted-foreground">{t('news.dimSignal', 'Signal')}</span>
                <span className="font-medium">{dims.signal ?? '-'}/100</span>
              </div>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export default function NewsCard({ article, compact = false, className, navigationContext }: NewsCardProps) {
  const { t } = useTranslation('dashboard')
  const location = useLocation()
  const sentiment = article.sentiment ? SENTIMENT_CONFIG[article.sentiment] : null
  const SentimentIcon = sentiment?.icon

  const handleOpenArticle = useCallback(() => {
    window.open(article.url, '_blank', 'noopener,noreferrer')
  }, [article.url])

  // Compact mode: used in sidebar/widget contexts
  if (compact) {
    return (
      <div
        className={cn(
          'flex items-start gap-3 p-3 rounded-lg hover:bg-accent/50 transition-colors cursor-pointer',
          className
        )}
        onClick={handleOpenArticle}
      >
        {article.imageUrl && (
          <img
            src={article.imageUrl}
            alt=""
            className="w-16 h-16 rounded object-cover flex-shrink-0"
          />
        )}
        <div className="flex-1 min-w-0">
          <h4 className="font-medium text-sm line-clamp-2 mb-1">{decodeEntities(article.title)}</h4>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{article.source}</span>
            <span>-</span>
            <Clock className="h-3 w-3" />
            <span>{formatRelativeTime(article.publishedAt)}</span>
            {sentiment && SentimentIcon && (
              <>
                <span>-</span>
                <SentimentIcon className={cn('h-3 w-3', sentiment.color)} />
              </>
            )}
          </div>
        </div>
      </div>
    )
  }

  // Full mode: compact clickable row → navigates to reader page
  const hasReaderContent = !!(article.detailedSummary || article.investmentSummary || article.aiAnalysis)
  const isProcessing = article.contentStatus === 'fetched' || article.contentStatus === 'partial'
  const summaryText = article.investmentSummary ?? article.summary

  const cardContent = (
    <>
      {/* Meta row */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1.5">
        <span className="font-medium">{article.source}</span>
        <span className="flex items-center gap-1">
          <Clock className="h-3 w-3" />
          {formatRelativeTime(article.publishedAt)}
        </span>
        <StatusBadge article={article} />
        {isProcessing && (
          <span className="inline-flex items-center gap-1 text-yellow-500">
            <Loader2 className="h-3 w-3 animate-spin" />
            {t('news.processing')}
          </span>
        )}
        {!hasReaderContent && !isProcessing && (
          <span className="inline-flex items-center gap-0.5 ml-auto opacity-0 group-hover:opacity-60 transition-opacity">
            <ExternalLink className="h-3 w-3" />
          </span>
        )}
      </div>

      {/* Title */}
      <h3 className="font-semibold text-[15px] leading-snug line-clamp-2 mb-1 group-hover:text-primary transition-colors">
        {decodeEntities(article.title)}
      </h3>

      {/* Summary */}
      {summaryText && (
        <p className="text-sm text-muted-foreground line-clamp-1 mb-2">
          {decodeEntities(summaryText)}
        </p>
      )}

      {/* Tags row */}
      {(article.symbol && article.symbol !== 'MARKET' || article.relatedEntities?.length || article.sentimentTag) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {article.symbol && article.symbol !== 'MARKET' && (
            <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
              {article.symbol}
            </span>
          )}
          {article.relatedEntities?.filter(e => e.type === 'stock' && e.entity !== article.symbol).slice(0, 3).map((entity) => (
            <span
              key={entity.entity}
              className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
            >
              {entity.entity}
            </span>
          ))}
          {article.sentimentTag && (
            <span
              className={cn(
                'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
                article.sentimentTag === 'bullish' && 'bg-green-500/10 text-green-500',
                article.sentimentTag === 'bearish' && 'bg-red-500/10 text-red-500',
                article.sentimentTag === 'neutral' && 'bg-blue-500/10 text-blue-400',
              )}
            >
              {article.sentimentTag}
            </span>
          )}
        </div>
      )}
    </>
  )

  return (
    <div className={cn('group border-b border-border/50 last:border-b-0', className)}>
      {hasReaderContent ? (
        <Link
          to={`/news/${article.id}`}
          state={{ article, navigation: navigationContext, origin: location.pathname + location.search }}
          className="block px-1 py-4 hover:bg-accent/30 transition-colors rounded-sm -mx-1"
        >
          {cardContent}
        </Link>
      ) : (
        <div
          className="px-1 py-4 hover:bg-accent/30 transition-colors rounded-sm -mx-1 cursor-pointer"
          onClick={handleOpenArticle}
        >
          {cardContent}
        </div>
      )}
    </div>
  )
}
