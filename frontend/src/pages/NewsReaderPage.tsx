import { Component, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams, useLocation, useNavigate, Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, ExternalLink, Clock, Loader2, FileWarning, Brain } from 'lucide-react'
import { useDrag } from '@use-gesture/react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import { formatRelativeTime, decodeHtmlEntities } from '@/lib/utils'
import { newsApi } from '@/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import ArticleScoreBadge from '@/components/news/ArticleScoreBadge'
import ArticleNavigation from '@/components/news/ArticleNavigation'
import type { NewsArticle, NewsNavigationContext } from '@/types'

interface LocationState {
  article?: NewsArticle
  navigation?: NewsNavigationContext
  defaultTab?: string
  origin?: string
}

/** Lightweight error boundary for markdown rendering — shows raw text on failure */
class MarkdownErrorBoundary extends Component<
  { fallbackText: string; children: ReactNode },
  { hasError: boolean }
> {
  state = { hasError: false }
  static getDerivedStateFromError() { return { hasError: true } }
  override render() {
    if (this.state.hasError) {
      return <pre className="whitespace-pre-wrap text-sm text-muted-foreground">{this.props.fallbackText}</pre>
    }
    return this.props.children
  }
}

type AnalysisStatus = 'idle' | 'streaming' | 'complete' | 'error'

export default function NewsReaderPage() {
  const { newsId } = useParams<{ newsId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')
  const queryClient = useQueryClient()

  const locationState = location.state as LocationState | undefined
  const passedArticle = locationState?.article
  const navigation = locationState?.navigation
  const defaultTab = locationState?.defaultTab
  const origin = locationState?.origin ?? '/news'

  // Fallback: fetch from API when no article in router state (refresh, direct URL)
  const { data: fetchedArticle, isLoading, isError } = useQuery({
    queryKey: ['news', 'article', newsId],
    queryFn: () => newsApi.getArticle(newsId!),
    enabled: !passedArticle && !!newsId,
    staleTime: 5 * 60 * 1000,
    retry: 2,
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
  })

  const article = passedArticle ?? fetchedArticle

  // On-demand analysis state
  const [analysisContent, setAnalysisContent] = useState('')
  const [analysisStatus, setAnalysisStatus] = useState<AnalysisStatus>('idle')
  const abortRef = useRef<AbortController | null>(null)
  const newsIdRef = useRef(newsId)
  newsIdRef.current = newsId

  // Seed React Query cache when article comes from router state (passedArticle)
  // so that streaming completion can find & update the cached entry
  useEffect(() => {
    if (passedArticle && newsId) {
      queryClient.setQueryData(['news', 'article', newsId], passedArticle)
    }
  }, [passedArticle, newsId, queryClient])

  // Effective analysis: prefer streaming content (re-analysis) over cached DB
  const effectiveAnalysis = analysisContent || article?.aiAnalysis

  // Determine which tabs to show and the default
  const { tabs, resolvedDefault } = useMemo(() => {
    if (!article) return { tabs: [] as string[], resolvedDefault: 'summary' }

    const available: string[] = []
    if (article.investmentSummary || article.summary) available.push('summary')
    if (article.detailedSummary) available.push('detailed')
    // Analysis tab is always available
    available.push('analysis')

    // If nothing available except analysis, add summary too
    if (available.length === 1) available.unshift('summary')

    let def = defaultTab
    if (!def || !available.includes(def)) {
      // Auto-select: prefer detailed > summary (analysis requires manual trigger)
      if (available.includes('detailed')) def = 'detailed'
      else def = 'summary'
    }

    return { tabs: available, resolvedDefault: def }
  }, [article, defaultTab])

  // Start streaming analysis
  const startAnalysis = useCallback((forceNew = false) => {
    if (!newsId) return

    // Capture newsId for stale-closure guard
    const targetId = newsId

    // Cancel any previous stream
    abortRef.current?.abort()

    setAnalysisContent('')
    setAnalysisStatus('streaming')

    const controller = newsApi.streamNewsAnalysis(
      newsId,
      (data) => {
        // Stale-closure guard: ignore events if user navigated to a different article
        if (newsIdRef.current !== targetId) return

        const type = data.type as string
        if (type === 'analysis_chunk') {
          const chunk = (data.data as Record<string, unknown>)?.content as string
          if (chunk) {
            setAnalysisContent(prev => prev + chunk)
          }
        } else if (type === 'complete') {
          setAnalysisStatus('complete')
          const report = (data.data as Record<string, unknown>)?.report as string
          if (report) {
            setAnalysisContent(report)
            // Update React Query cache so the article shows as analyzed
            queryClient.setQueryData<NewsArticle>(
              ['news', 'article', targetId],
              (old) => old ? { ...old, aiAnalysis: report } : old,
            )
          }
          // M4: complete without report — keep whatever streamed content we have
        } else if (type === 'error') {
          setAnalysisStatus('error')
        }
      },
      () => {
        if (newsIdRef.current !== targetId) return
        setAnalysisStatus(prev => prev === 'streaming' ? 'error' : prev)
      },
      () => {
        // onDone — stream ended; only treat as error if we got no content at all
        if (newsIdRef.current !== targetId) return
        setAnalysisStatus(prev => {
          if (prev !== 'streaming') return prev
          // If we accumulated content but never got a 'complete' event,
          // treat as complete (premature close with partial content is OK)
          return 'complete'
        })
      },
      { forceNew },
    )

    abortRef.current = controller
  }, [newsId, queryClient])

  // Reset analysis state and cleanup on article change
  useEffect(() => {
    setAnalysisContent('')
    setAnalysisStatus('idle')
    return () => {
      abortRef.current?.abort()
    }
  }, [newsId])

  // Swipe gesture hooks - must be called unconditionally before any returns
  const isMobile = useIsMobile()
  const articleRef = useRef<HTMLElement>(null)
  const [articleSwipeOffset, setArticleSwipeOffset] = useState(0)

  const currentIndex = navigation?.currentIndex ?? -1
  const navArticles = navigation?.articles ?? []
  const canGoPrev = navigation != null && currentIndex > 0
  const canGoNext = navigation != null && currentIndex < navArticles.length - 1

  useDrag(
    ({ active, movement: [mx], velocity: [vx], direction: [dx], cancel }) => {
      if (!isMobile || !navigation) {
        cancel()
        return
      }
      if (active) {
        // Limit swipe range if at boundary
        if (mx < 0 && !canGoNext) {
          setArticleSwipeOffset(0)
          return
        }
        if (mx > 0 && !canGoPrev) {
          setArticleSwipeOffset(0)
          return
        }
        setArticleSwipeOffset(mx)
      } else {
        // Left swipe: next article
        if (mx < -80 && vx > 0.3 && dx < 0 && canGoNext) {
          const nextArticle = navArticles[currentIndex + 1]
          if (nextArticle) {
            navigate(`/news/${nextArticle.id}`, {
              state: {
                navigation: { articles: navArticles, currentIndex: currentIndex + 1 },
                origin,
              },
            })
          }
        }
        // Right swipe: previous article
        if (mx > 80 && vx > 0.3 && dx > 0 && canGoPrev) {
          const prevArticle = navArticles[currentIndex - 1]
          if (prevArticle) {
            navigate(`/news/${prevArticle.id}`, {
              state: {
                navigation: { articles: navArticles, currentIndex: currentIndex - 1 },
                origin,
              },
            })
          }
        }
        setArticleSwipeOffset(0)
      }
    },
    {
      target: articleRef,
      filterTaps: true,
      axis: 'x',
      enabled: isMobile && navigation != null,
    }
  )

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-[50vh]">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  // Error / not found state
  if (isError || !article) {
    return (
      <div className="max-w-[720px] mx-auto px-4 py-16 text-center">
        <FileWarning className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
        <h2 className="text-lg font-semibold mb-2">{t('news.reader.articleNotFound')}</h2>
        <p className="text-sm text-muted-foreground mb-6">{t('news.reader.articleNotFoundDesc')}</p>
        <Button
          variant="outline"
          onClick={() => navigate(origin)}
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          {t('news.reader.backToNews')}
        </Button>
      </div>
    )
  }

  return (
    <article
      ref={articleRef}
      className="max-w-[720px] mx-auto px-4 py-8"
      style={{
        transform: articleSwipeOffset !== 0 ? `translateX(${articleSwipeOffset}px)` : undefined,
        transition: articleSwipeOffset !== 0 ? 'none' : 'transform 0.3s ease-out',
        touchAction: 'pan-y',
      }}
    >
      {/* Top bar: back + original link */}
      <div className="flex items-center justify-between mb-8">
        <Button
          variant="ghost"
          size="sm"
          className="-ml-2"
          onClick={() => navigate(origin)}
        >
          <ArrowLeft className="mr-2 h-4 w-4" />
          {t('news.reader.backToNews')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => window.open(article.url, '_blank', 'noopener,noreferrer')}
        >
          <ExternalLink className="mr-2 h-4 w-4" />
          {t('news.reader.viewOriginal')}
        </Button>
      </div>

      {/* Title */}
      <h1 className="text-2xl md:text-3xl font-bold tracking-tight leading-tight mb-4">
        {decodeHtmlEntities(article.title)}
      </h1>

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground mb-2">
        <span className="font-medium text-foreground/70">{article.source}</span>
        <span className="flex items-center gap-1">
          <Clock className="h-3.5 w-3.5" />
          {formatRelativeTime(article.publishedAt)}
        </span>
        {article.symbol && article.symbol !== 'MARKET' && (
          <Link
            to={`/stock/${article.symbol}`}
            className="text-primary hover:underline font-medium"
          >
            {article.symbol}
          </Link>
        )}
        <ArticleScoreBadge article={article} />
      </div>

      {/* Tags row */}
      {(article.relatedEntities?.length || article.sentimentTag) && (
        <div className="flex flex-wrap gap-1.5 mb-4">
          {article.relatedEntities
            ?.filter(e => e.type === 'stock' && e.entity !== article.symbol)
            .map((entity) => (
              <Link
                key={entity.entity}
                to={`/stock/${entity.entity}`}
                className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground hover:bg-muted/80 transition-colors"
              >
                {entity.entity}
              </Link>
            ))}
          {article.sentimentTag && (
            <span
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${
                article.sentimentTag === 'bullish'
                  ? 'bg-green-500/10 text-green-500'
                  : article.sentimentTag === 'bearish'
                    ? 'bg-red-500/10 text-red-500'
                    : 'bg-blue-500/10 text-blue-400'
              }`}
            >
              {article.sentimentTag}
            </span>
          )}
        </div>
      )}

      <Separator className="my-6" />

      {/* Content tabs */}
      <Tabs defaultValue={resolvedDefault} className="w-full">
        <TabsList className="mb-6">
          {tabs.includes('summary') && (
            <TabsTrigger value="summary">{t('news.reader.summary')}</TabsTrigger>
          )}
          {tabs.includes('detailed') && (
            <TabsTrigger value="detailed">{t('news.reader.detailed')}</TabsTrigger>
          )}
          {tabs.includes('analysis') && (
            <TabsTrigger value="analysis">{t('news.reader.analysis')}</TabsTrigger>
          )}
        </TabsList>

        {tabs.includes('summary') && (
          <TabsContent value="summary">
            <div className="prose dark:prose-invert max-w-none">
              <p className="text-lg leading-relaxed">
                {decodeHtmlEntities(article.investmentSummary ?? article.summary ?? t('news.reader.noContent'))}
              </p>
            </div>
          </TabsContent>
        )}

        {tabs.includes('detailed') && (
          <TabsContent value="detailed">
            <div className="prose dark:prose-invert max-w-none">
              <div className="whitespace-pre-wrap leading-loose">
                {article.detailedSummary}
              </div>
            </div>
          </TabsContent>
        )}

        {tabs.includes('analysis') && (
          <TabsContent value="analysis">
            <div className="prose dark:prose-invert max-w-none">
              {effectiveAnalysis ? (
                <>
                  <MarkdownErrorBoundary fallbackText={effectiveAnalysis}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {effectiveAnalysis}
                    </ReactMarkdown>
                  </MarkdownErrorBoundary>
                  {analysisStatus === 'streaming' && (
                    <div className="flex items-center gap-2 mt-4 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {t('news.reader.generating')}
                    </div>
                  )}
                </>
              ) : analysisStatus === 'error' ? (
                <div className="flex flex-col items-center py-12 text-center">
                  <p className="text-sm text-destructive mb-4">{t('news.reader.analysisError')}</p>
                  <Button variant="outline" size="sm" onClick={() => startAnalysis(true)}>
                    {t('news.reader.retryAnalysis')}
                  </Button>
                </div>
              ) : analysisStatus === 'streaming' ? (
                <div className="flex items-center justify-center py-12">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="flex flex-col items-center py-12 text-center">
                  <Brain className="h-10 w-10 text-muted-foreground/50 mb-3" />
                  <p className="text-sm text-muted-foreground mb-4">{t('news.reader.noAnalysisYet')}</p>
                  <Button variant="outline" size="sm" onClick={() => startAnalysis()}>
                    <Brain className="mr-2 h-4 w-4" />
                    {t('news.reader.generateAnalysis')}
                  </Button>
                </div>
              )}
            </div>
          </TabsContent>
        )}
      </Tabs>

      {/* Bottom navigation */}
      {navigation && (
        <>
          <Separator className="mt-8" />
          <ArticleNavigation navigation={navigation} origin={origin} />
        </>
      )}
    </article>
  )
}
