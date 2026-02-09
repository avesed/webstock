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
import { cn } from '@/lib/utils'
import { formatRelativeTime, truncate } from '@/lib/utils'
import { newsApi } from '@/api'
import { useToast } from '@/hooks'
import type { NewsArticle, NewsSentiment } from '@/types'

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

export default function NewsCard({ article, compact = false, className, onSymbolClick }: NewsCardProps) {
  const { toast } = useToast()
  const { t, i18n } = useTranslation('dashboard')
  const [isAnalysisOpen, setIsAnalysisOpen] = useState(false)
  const [analysisContent, setAnalysisContent] = useState<string | null>(article.aiAnalysis ?? null)

  // Get current language for API call (zh or en)
  const language = i18n.language?.startsWith('zh') ? 'zh' : 'en'

  // Analyze article mutation
  const analyzeMutation = useMutation({
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

  const handleAnalyze = useCallback(() => {
    if (analysisContent) {
      setIsAnalysisOpen(true)
    } else {
      analyzeMutation.mutate()
    }
  }, [analysisContent, analyzeMutation])

  const handleOpenArticle = useCallback(() => {
    window.open(article.url, '_blank', 'noopener,noreferrer')
  }, [article.url])

  const sentiment = article.sentiment ? SENTIMENT_CONFIG[article.sentiment] : null
  const SentimentIcon = sentiment?.icon

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
          <h4 className="font-medium text-sm line-clamp-2 mb-1">{article.title}</h4>
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
              <CardTitle className="text-lg line-clamp-2 mb-2">{article.title}</CardTitle>
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="font-medium">{article.source}</span>
                <span>-</span>
                <Clock className="h-4 w-4" />
                <span>{formatRelativeTime(article.publishedAt)}</span>
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
        {article.summary && (
          <CardContent className="pt-0">
            <CardDescription className="line-clamp-3">
              {article.summary}
            </CardDescription>
          </CardContent>
        )}
        <CardContent className="pt-0 flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={handleOpenArticle}>
            <ExternalLink className="mr-2 h-4 w-4" />
            {t('news.readMore', 'Read More')}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={handleAnalyze}
            disabled={analyzeMutation.isPending}
          >
            {analyzeMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Brain className="mr-2 h-4 w-4" />
            )}
            {analysisContent ? t('news.viewAnalysis', 'View Analysis') : t('news.analyze', 'Analyze')}
          </Button>
        </CardContent>
      </Card>

      {/* AI Analysis Dialog */}
      <Dialog open={isAnalysisOpen} onOpenChange={setIsAnalysisOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Brain className="h-5 w-5" />
              {t('news.aiAnalysis', 'AI Analysis')}
            </DialogTitle>
            <DialogDescription>
              {truncate(article.title, 100)}
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
    </>
  )
}
