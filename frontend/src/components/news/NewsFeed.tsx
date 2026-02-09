import { useState, useCallback, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, AlertCircle, Newspaper, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { newsApi } from '@/api'
import NewsCard from './NewsCard'
import type { NewsArticle } from '@/types'

interface NewsFeedProps {
  symbol?: string
  mode?: 'feed' | 'symbol' | 'trending' | 'market'
  compact?: boolean
  maxHeight?: string
  className?: string
}

export default function NewsFeed({
  symbol,
  mode = 'feed',
  compact = false,
  maxHeight = '600px',
  className,
}: NewsFeedProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')
  const loadMoreRef = useRef<HTMLDivElement>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)

  // Infinite query for paginated news
  const {
    data,
    isLoading,
    isError,
    error,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ['news', mode, symbol],
    queryFn: async ({ pageParam = 1 }) => {
      if (mode === 'trending') {
        // Trending doesn't support pagination
        const articles = await newsApi.getTrending()
        return {
          items: articles,
          total: articles.length,
          page: 1,
          pageSize: articles.length,
          totalPages: 1,
        }
      } else if (mode === 'market') {
        return newsApi.getMarket(pageParam, 10)
      } else if (mode === 'symbol' && symbol) {
        return newsApi.getBySymbol(symbol, pageParam, 10)
      } else {
        return newsApi.getFeed(pageParam, 10)
      }
    },
    getNextPageParam: (lastPage) => {
      if (mode === 'trending') return undefined
      if (lastPage.page < lastPage.totalPages) {
        return lastPage.page + 1
      }
      return undefined
    },
    initialPageParam: 1,
    enabled: mode !== 'symbol' || !!symbol,
  })

  // Intersection observer for infinite scroll
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          fetchNextPage()
        }
      },
      { threshold: 0.1 }
    )

    if (loadMoreRef.current) {
      observer.observe(loadMoreRef.current)
    }

    return () => observer.disconnect()
  }, [fetchNextPage, hasNextPage, isFetchingNextPage])

  const handleRefresh = useCallback(async () => {
    setIsRefreshing(true)
    await refetch()
    setIsRefreshing(false)
  }, [refetch])

  const handleSymbolClick = useCallback((clickedSymbol: string) => {
    navigate(`/stock/${clickedSymbol}`)
  }, [navigate])

  // Flatten all pages into a single array with validation
  const articles: NewsArticle[] = data?.pages.flatMap((page) => 
    Array.isArray(page?.items) ? page.items.filter((item): item is NewsArticle => 
      item != null && typeof item === 'object' && 'id' in item
    ) : []
  ) ?? []

  if (isLoading) {
    return (
      <div className={cn('flex items-center justify-center p-8', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-8', className)}>
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">
          {error instanceof Error ? error.message : t('news.noNews')}
        </p>
        <Button variant="outline" size="sm" onClick={handleRefresh}>
          {t('common:actions.retry', 'Try again')}
        </Button>
      </div>
    )
  }

  if (articles.length === 0) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-8', className)}>
        <Newspaper className="h-12 w-12 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">
          {symbol ? t('news.noNewsForSymbol', { symbol }) : t('news.noNews')}
        </p>
      </div>
    )
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Refresh button */}
      <div className="flex justify-end">
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={isRefreshing}
        >
          <RefreshCw className={cn('h-4 w-4 mr-2', isRefreshing && 'animate-spin')} />
          {t('news.refresh')}
        </Button>
      </div>

      {/* News list */}
      <div
        style={{ maxHeight }}
        className="overflow-y-auto pr-2 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent"
      >
        <div className={cn('space-y-4', compact && 'space-y-1')}>
          {articles.map((article) => (
            <NewsCard
              key={article.id}
              article={article}
              compact={compact}
              onSymbolClick={handleSymbolClick}
            />
          ))}

          {/* Load more trigger */}
          <div ref={loadMoreRef} className="py-4">
            {isFetchingNextPage && (
              <div className="flex items-center justify-center">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            )}
            {!hasNextPage && articles.length > 0 && mode !== 'trending' && (
              <p className="text-center text-sm text-muted-foreground">
                {t('news.noMoreArticles')}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
