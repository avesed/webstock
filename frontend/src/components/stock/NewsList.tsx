import { cn, formatDate } from '@/lib/utils'
import type { NewsArticle } from '@/types'

interface NewsListProps {
  articles: NewsArticle[]
}

/**
 * News list component for displaying news articles.
 * Shows article title, summary, source, date, and sentiment.
 */
export function NewsList({ articles }: NewsListProps) {
  return (
    <div className="space-y-4">
      {articles.map((article) => (
        <a
          key={article.id}
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block rounded-lg border p-4 transition-colors hover:bg-accent/50"
        >
          <div className="flex gap-4">
            {article.imageUrl && (
              <img
                src={article.imageUrl}
                alt=""
                className="h-20 w-20 rounded object-cover"
              />
            )}
            <div className="flex-1 min-w-0">
              <h4 className="font-medium line-clamp-2">{article.title}</h4>
              {article.summary && (
                <p className="mt-1 text-sm text-muted-foreground line-clamp-2">
                  {article.summary}
                </p>
              )}
              <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                <span>{article.source}</span>
                <span>-</span>
                <span>{formatDate(article.publishedAt)}</span>
                {article.sentiment && (
                  <span
                    className={cn(
                      'rounded px-1.5 py-0.5',
                      article.sentiment === 'POSITIVE'
                        ? 'bg-stock-up/10 text-stock-up'
                        : article.sentiment === 'NEGATIVE'
                        ? 'bg-stock-down/10 text-stock-down'
                        : 'bg-muted text-muted-foreground'
                    )}
                  >
                    {article.sentiment.toLowerCase()}
                  </span>
                )}
              </div>
            </div>
          </div>
        </a>
      ))}
    </div>
  )
}
