import { useMemo } from 'react'
import { useParams, useLocation, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ArrowLeft, ExternalLink, Clock, Loader2, FileWarning } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Separator } from '@/components/ui/separator'
import { formatRelativeTime, decodeHtmlEntities } from '@/lib/utils'
import { newsApi } from '@/api'
import ArticleScoreBadge from '@/components/news/ArticleScoreBadge'
import ArticleNavigation from '@/components/news/ArticleNavigation'
import type { NewsArticle, NewsNavigationContext } from '@/types'

interface LocationState {
  article?: NewsArticle
  navigation?: NewsNavigationContext
  defaultTab?: string
  origin?: string
}

export default function NewsReaderPage() {
  const { newsId } = useParams<{ newsId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')

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
  })

  const article = passedArticle ?? fetchedArticle

  // Determine which tabs to show and the default
  const { tabs, resolvedDefault } = useMemo(() => {
    if (!article) return { tabs: [] as string[], resolvedDefault: 'summary' }

    const available: string[] = []
    if (article.investmentSummary || article.summary) available.push('summary')
    if (article.detailedSummary) available.push('detailed')
    if (article.aiAnalysis) available.push('analysis')

    // If nothing available, at least show summary tab
    if (available.length === 0) available.push('summary')

    let def = defaultTab
    if (!def || !available.includes(def)) {
      // Auto-select: prefer detailed > analysis > summary
      if (available.includes('detailed')) def = 'detailed'
      else if (available.includes('analysis')) def = 'analysis'
      else def = 'summary'
    }

    return { tabs: available, resolvedDefault: def }
  }, [article, defaultTab])

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
    <article className="max-w-[720px] mx-auto px-4 py-8">
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
            <div className="prose prose-lg dark:prose-invert max-w-none">
              <p className="text-lg leading-relaxed">
                {decodeHtmlEntities(article.investmentSummary ?? article.summary ?? t('news.reader.noContent'))}
              </p>
            </div>
          </TabsContent>
        )}

        {tabs.includes('detailed') && (
          <TabsContent value="detailed">
            <div className="prose prose-lg dark:prose-invert max-w-none">
              <div className="whitespace-pre-wrap leading-loose">
                {article.detailedSummary}
              </div>
            </div>
          </TabsContent>
        )}

        {tabs.includes('analysis') && (
          <TabsContent value="analysis">
            <div className="prose prose-lg dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {article.aiAnalysis ?? ''}
              </ReactMarkdown>
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
