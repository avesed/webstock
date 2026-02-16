import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { cn, formatRelativeTime, decodeHtmlEntities } from '@/lib/utils'
import { useIsMobile } from '@/hooks/useIsMobile'
import type { NewsArticle, NewsSentiment } from '@/types'

interface CompactNewsListProps {
  newsData: NewsArticle[] | undefined
  isLoading: boolean
}

function getSentimentDotColor(sentiment: NewsSentiment | undefined): string {
  switch (sentiment) {
    case 'POSITIVE':
      return 'bg-green-500'
    case 'NEGATIVE':
      return 'bg-red-500'
    default:
      return 'bg-gray-400'
  }
}

export default function CompactNewsList({ newsData, isLoading }: CompactNewsListProps) {
  const navigate = useNavigate()
  const { t, i18n } = useTranslation('dashboard')
  const isMobile = useIsMobile()

  const maxItems = isMobile ? 4 : 8
  const displayedNews = newsData?.slice(0, maxItems)

  const handleRowClick = (article: NewsArticle) => {
    navigate(`/news/${article.id}`)
  }

  return (
    <div className="h-full rounded-lg border bg-card p-3 overflow-hidden">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t('compactNews.title')}
        </h3>
        <button
          type="button"
          className="bg-transparent border-0 p-0 text-xs text-primary cursor-pointer hover:underline"
          onClick={() => navigate('/news')}
        >
          {t('compactNews.viewAll')}
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-6">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : !displayedNews || displayedNews.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <span className="text-sm text-muted-foreground">
            {t('news.noNews')}
          </span>
        </div>
      ) : (
        <div className="space-y-0.5">
          {displayedNews.map((article) => (
            <button
              type="button"
              key={article.id}
              className="w-full bg-transparent border-0 text-left flex items-center gap-2 py-1.5 px-2 rounded cursor-pointer hover:bg-accent/50 transition-colors"
              onClick={() => handleRowClick(article)}
            >
              {article.symbol && article.symbol !== 'MARKET' && (
                <span className="text-[10px] font-mono bg-muted px-1.5 py-0.5 rounded shrink-0">
                  {article.symbol}
                </span>
              )}

              <span className="text-sm truncate flex-1">
                {decodeHtmlEntities(article.title)}
              </span>

              <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">
                {article.publishedAt
                  ? formatRelativeTime(article.publishedAt, i18n.language)
                  : '--'}
              </span>

              <span
                className={cn(
                  'w-1.5 h-1.5 rounded-full shrink-0',
                  getSentimentDotColor(article.sentiment)
                )}
              />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
