import { useState, useCallback } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import DOMPurify from 'dompurify'
import {
  ExternalLink,
  Clock,
  Brain,
  Loader2,
  TrendingUp,
  TrendingDown,
  Minus,
  Zap,
  Ban,
  AlertTriangle,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn, formatRelativeTime, truncate } from '@/lib/utils'
import { newsApi } from '@/api'
import { useToast } from '@/hooks'
import type { NewsArticle, NewsSentiment } from '@/types'
import DetailedSummaryDialog from './DetailedSummaryDialog'
import AnalysisReportDialog from './AnalysisReportDialog'

const HTML_ENTITY_MAP: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
  '&apos;': "'",
}

function decodeEntities(text: string): string {
  return text.replace(
    /&(?:amp|lt|gt|quot|#39|apos);/g,
    (match) => HTML_ENTITY_MAP[match] ?? match
  )
}

interface NewsCardProps {
  article: NewsArticle
  compact?: boolean
  className?: string
  onSymbolClick?: (symbol: string) => void
}

const SENTIMENT_CONFIG: Record<NewsSentiment, { icon: typeof TrendingUp; color: string; translationKey: string }> = {
  POSITIVE: { icon: TrendingUp, color: 'text-stock-up', translationKey: 'news.positive' },
  NEGATIVE: { icon: TrendingDown, color: 'text-stock-down', translationKey: 'news.negative' },
  NEUTRAL: { icon: Minus, color: 'text-muted-foreground', translationKey: 'news.neutral' },
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

  // No badge for articles without any pipeline status
  // (live external news from Finnhub/AKShare trending feed)
  if (!cs && !fs && score == null && !path) return null

  // Resolve badge appearance based on article lifecycle status
  let text: string
  let label: string | undefined
  let color: string
  let Icon: typeof Zap | undefined

  if (path === 'error') {
    // Scoring failed — no score assigned
    text = t('news.statusScoringError', 'Score Error')
    color = 'bg-orange-500/10 text-orange-400 border-orange-500/20'
    Icon = AlertTriangle
  } else if (score != null && path) {
    // Phase 2 scored articles
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
    // Successfully processed without Phase 2 scoring — no badge needed
    // (the article being visible already implies it was kept)
    return null
  } else {
    text = t('news.statusPending', 'Pending')
    color = 'bg-muted/50 text-muted-foreground border-border'
    Icon = Clock
  }

  const hasTooltip = !!(dims || details?.reasoning)

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
            {details?.reasoning && (
              <p className="text-muted-foreground italic border-t border-border/50 pt-1">
                {details.reasoning.length > 120 ? details.reasoning.slice(0, 120) + '…' : details.reasoning}
              </p>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export default function NewsCard({ article, compact = false, className, onSymbolClick }: NewsCardProps) {
  const { toast } = useToast()
  const { t, i18n } = useTranslation('dashboard')
  const [isAnalysisOpen, setIsAnalysisOpen] = useState(false)
  const [analysisContent, setAnalysisContent] = useState<string | null>(article.aiAnalysis ?? null)
  const [showDetailedSummary, setShowDetailedSummary] = useState(false)
  const [showAnalysisReport, setShowAnalysisReport] = useState(false)

  // Get current language for API call (zh or en)
  const language = i18n.language?.startsWith('zh') ? 'zh' : 'en'

  // Analyze article mutation (on-demand fallback for articles without pre-generated analysis)
  const { mutate: analyzeArticle, isPending: isAnalyzing } = useMutation({
    mutationFn: () => newsApi.analyzeArticle(article, language),
    onSuccess: (updatedArticle) => {
      setAnalysisContent(updatedArticle.aiAnalysis ?? null)
      setIsAnalysisOpen(true)
    },
    onError: () => {
      toast({
        title: t('news.analysisFailed', 'Analysis failed'),
        description: t('news.analysisFailedDesc', 'Could not analyze this article. Please try again.'),
        variant: 'destructive',
      })
    },
  })

  // "Read More" button: open detailed summary dialog if available, otherwise open original URL
  const handleReadMore = useCallback(() => {
    if (article.detailedSummary) {
      setShowDetailedSummary(true)
    } else {
      window.open(article.url, '_blank', 'noopener,noreferrer')
    }
  }, [article.detailedSummary, article.url])

  // "Analyze" button: open pre-generated analysis report, or fall back to on-demand API / legacy dialog
  const handleAnalyze = useCallback(() => {
    if (article.aiAnalysis) {
      // Pre-generated analysis available: open the rich markdown dialog
      setShowAnalysisReport(true)
    } else if (analysisContent) {
      // On-demand analysis result cached from a previous mutation call: use legacy dialog
      setIsAnalysisOpen(true)
    } else {
      // No analysis at all: trigger on-demand API call (legacy behavior)
      analyzeArticle()
    }
  }, [article.aiAnalysis, analysisContent, analyzeArticle])

  const handleOpenArticle = useCallback(() => {
    window.open(article.url, '_blank', 'noopener,noreferrer')
  }, [article.url])

  const sentiment = article.sentiment ? SENTIMENT_CONFIG[article.sentiment] : null
  const SentimentIcon = sentiment?.icon

  // Determine button labels based on content availability
  const readMoreLabel = article.detailedSummary
    ? t('news.readMore', 'Read More')
    : t('news.openOriginal', 'Open Original')
  const analyzeLabel = (article.aiAnalysis || analysisContent)
    ? t('news.viewAnalysis', 'View Analysis')
    : t('news.analyze', 'Analyze')

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

  return (
    <>
      <Card className={cn('overflow-hidden', className)}>
        {article.imageUrl && (
          <div className="relative h-48 overflow-hidden">
            <img
              src={article.imageUrl}
              alt=""
              className="w-full h-full object-cover"
            />
            {sentiment && (
              <div
                className={cn(
                  'absolute top-2 right-2 rounded-full px-2 py-1 text-xs font-medium flex items-center gap-1',
                  sentiment.color === 'text-stock-up' && 'bg-stock-up/20',
                  sentiment.color === 'text-stock-down' && 'bg-stock-down/20',
                  sentiment.color === 'text-muted-foreground' && 'bg-muted'
                )}
              >
                {SentimentIcon && <SentimentIcon className={cn('h-3 w-3', sentiment.color)} />}
                <span className={sentiment.color}>{t(sentiment.translationKey as 'news.positive' | 'news.negative' | 'news.neutral')}</span>
              </div>
            )}
          </div>
        )}
        <CardHeader className={article.imageUrl ? 'pt-4' : ''}>
          <div className="flex items-start justify-between gap-2">
            <div className="flex-1">
              <CardTitle className="text-lg line-clamp-2 mb-2">{decodeEntities(article.title)}</CardTitle>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="font-medium">{article.source}</span>
                <span>-</span>
                <Clock className="h-4 w-4" />
                <span>{formatRelativeTime(article.publishedAt)}</span>
                <StatusBadge article={article} />
              </div>
            </div>
            {!article.imageUrl && sentiment && SentimentIcon && (
              <div
                className={cn(
                  'rounded-full p-2',
                  sentiment.color === 'text-stock-up' && 'bg-stock-up/10',
                  sentiment.color === 'text-stock-down' && 'bg-stock-down/10',
                  sentiment.color === 'text-muted-foreground' && 'bg-muted'
                )}
              >
                <SentimentIcon className={cn('h-5 w-5', sentiment.color)} />
              </div>
            )}
          </div>
          {(article.symbol && article.symbol !== 'MARKET' || (article.relatedEntities && article.relatedEntities.length > 0)) && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {article.symbol && article.symbol !== 'MARKET' && (
                <span
                  className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary cursor-pointer hover:bg-primary/20 transition-colors"
                  onClick={(e) => {
                    e.stopPropagation()
                    onSymbolClick?.(article.symbol!)
                  }}
                >
                  {article.symbol}
                </span>
              )}
              {article.relatedEntities?.filter(e => e.type === 'stock' && e.entity !== article.symbol).map((entity) => (
                <span
                  key={entity.entity}
                  className="inline-flex items-center rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground cursor-pointer hover:bg-muted/80 transition-colors"
                  onClick={(e) => {
                    e.stopPropagation()
                    onSymbolClick?.(entity.entity)
                  }}
                >
                  {entity.entity}
                </span>
              ))}
              {article.sentimentTag && (
                <span
                  className={cn(
                    'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium',
                    article.sentimentTag === 'bullish' && 'bg-green-500/10 text-green-500',
                    article.sentimentTag === 'bearish' && 'bg-red-500/10 text-red-500',
                    article.sentimentTag === 'neutral' && 'bg-muted text-muted-foreground',
                  )}
                >
                  {article.sentimentTag}
                </span>
              )}
            </div>
          )}
        </CardHeader>
        {(article.investmentSummary || article.summary) && (
          <CardContent className="pt-0">
            {article.investmentSummary ? (
              <CardDescription className="text-sm font-medium text-foreground/80">
                {decodeEntities(article.investmentSummary)}
              </CardDescription>
            ) : (
              <CardDescription className="line-clamp-3">
                {decodeEntities(article.summary ?? '')}
              </CardDescription>
            )}
          </CardContent>
        )}
        <CardContent className="pt-0 flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleReadMore}>
            <ExternalLink className="mr-2 h-4 w-4" />
            {readMoreLabel}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleAnalyze}
            disabled={isAnalyzing}
          >
            {isAnalyzing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Brain className="mr-2 h-4 w-4" />
            )}
            {analyzeLabel}
          </Button>
        </CardContent>
      </Card>

      {/* Detailed Summary Dialog - shows pre-generated detailed summary */}
      {showDetailedSummary && (
        <DetailedSummaryDialog
          open={true}
          onOpenChange={setShowDetailedSummary}
          title={decodeEntities(article.title)}
          detailedSummary={article.detailedSummary ?? ''}
          originalUrl={article.url}
        />
      )}

      {/* Analysis Report Dialog - shows pre-generated Markdown analysis report */}
      {showAnalysisReport && (
        <AnalysisReportDialog
          open={true}
          onOpenChange={setShowAnalysisReport}
          title={decodeEntities(article.title)}
          analysisReport={article.aiAnalysis ?? ''}
          originalUrl={article.url}
        />
      )}

      {/* Legacy AI Analysis Dialog - for on-demand analysis results (backward compatibility) */}
      {isAnalysisOpen && (
        <Dialog open={true} onOpenChange={setIsAnalysisOpen}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Brain className="h-5 w-5" />
                {t('news.aiAnalysis', 'AI Analysis')}
              </DialogTitle>
              <DialogDescription>
                {truncate(decodeEntities(article.title), 100)}
              </DialogDescription>
            </DialogHeader>
            <ScrollArea className="max-h-[60vh]">
              <div className="prose prose-sm dark:prose-invert max-w-none">
                {analysisContent ? (
                  analysisContent.split('\n').map((line, index) => {
                    if (!line.trim()) return <br key={index} />

                    if (line.startsWith('###')) {
                      return (
                        <h4 key={index} className="text-base font-semibold mt-4 mb-2">
                          {line.replace(/^###\s*/, '')}
                        </h4>
                      )
                    }
                    if (line.startsWith('##')) {
                      return (
                        <h3 key={index} className="text-lg font-semibold mt-4 mb-2">
                          {line.replace(/^##\s*/, '')}
                        </h3>
                      )
                    }
                    if (line.startsWith('#')) {
                      return (
                        <h2 key={index} className="text-xl font-bold mt-4 mb-2">
                          {line.replace(/^#\s*/, '')}
                        </h2>
                      )
                    }

                    if (line.startsWith('- ') || line.startsWith('* ') || line.startsWith('• ')) {
                      return (
                        <li key={index} className="ml-4">
                          {line.replace(/^[-*•]\s*/, '')}
                        </li>
                      )
                    }

                    const formattedLine = line.replace(
                      /\*\*(.*?)\*\*/g,
                      '<strong>$1</strong>'
                    )
                    const sanitizedLine = DOMPurify.sanitize(formattedLine, {
                      ALLOWED_TAGS: ['strong', 'em', 'b', 'i'],
                      ALLOWED_ATTR: [],
                    })

                    return (
                      <p
                        key={index}
                        className="leading-relaxed"
                        dangerouslySetInnerHTML={{ __html: sanitizedLine }}
                      />
                    )
                  })
                ) : (
                  <p className="text-muted-foreground">{t('news.noAnalysis', 'No analysis available.')}</p>
                )}
              </div>
            </ScrollArea>
          </DialogContent>
        </Dialog>
      )}
    </>
  )
}
