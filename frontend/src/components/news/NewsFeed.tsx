import { useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useInfiniteQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, AlertCircle, Newspaper } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { PullToRefresh } from '@/components/ui/PullToRefresh'
import { cn } from '@/lib/utils'
import { newsApi } from '@/api'
import { useIsMobile } from '@/hooks/useIsMobile'
import NewsCard from './NewsCard'
import type { NewsArticle } from '@/types'

interface NewsFeedProps {
  symbol?: string
  mode?: 'feed' | 'symbol' | 'trending' | 'market'
  compact?: boolean
  maxHeight?: string
  className?: string
  filters?: {
    search?: string
    sentimentTag?: string
    market?: string
  }
}

export default function NewsFeed({
  symbol,
  mode = 'feed',
  compact = false,
  maxHeight,
  className,
  filters,
}: NewsFeedProps) {
  const navigate = useNavigate()
  const { t } = useTranslation('dashboard')
  const loadMoreRef = useRef<HTMLDivElement>(null)
  const isMobile = useIsMobile()

  const containerRef = useRef<HTMLDivElement>(null)

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
    queryKey: ['news', mode, symbol, filters?.search, filters?.sentimentTag, filters?.market],
    queryFn: async ({ pageParam = 1 }) => {
      if (mode === 'trending') {
        const articles = await newsApi.getTrending()
        return {
          items: articles,
          total: articles.length,
          page: 1,
          pageSize: articles.length,
          totalPages: 1,
        }
      } else if (mode === 'market') {
        return newsApi.getMarket(pageParam, 10, filters)
      } else if (mode === 'symbol' && symbol) {
        return newsApi.getBySymbol(symbol, pageParam, 10)
      } else {
        const feedFilters: { search?: string; sentimentTag?: string } = {}
        if (filters?.search) feedFilters.search = filters.search
        if (filters?.sentimentTag) feedFilters.sentimentTag = filters.sentimentTag
        return newsApi.getFeed(pageParam, 10, Object.keys(feedFilters).length > 0 ? feedFilters : undefined)
      }
    },
    getNextPageParam: (lastPage) => {
      if (mode === 'trending') return undefined
      // Use totalPages directly — derived from backend's hasMore flag
      if (lastPage.page < lastPage.totalPages) {
        return lastPage.page + 1
      }
      return undefined
    },
    initialPageParam: 1,
    enabled: mode !== 'symbol' || !!symbol,
    // Show previous data while new filter results load (avoids flash of empty)
    placeholderData: (prev) => prev,
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

  // Scroll to top when filters change (new query starts)
  useEffect(() => {
    containerRef.current?.scrollTo({ top: 0 })
  }, [filters?.search, filters?.sentimentTag, filters?.market])

  // Restore scroll position when returning from article reader (full-page mode only)
  const shouldRestoreScroll = !compact && !maxHeight
  useEffect(() => {
    if (!shouldRestoreScroll) return
    const saved = sessionStorage.getItem('news-feed-scroll')
    if (saved == null) return
    sessionStorage.removeItem('news-feed-scroll')
    // Double rAF ensures cached React Query data has rendered before scrolling
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const main = document.querySelector('main')
        if (main) main.scrollTop = Number(saved)
      })
    })
  }, [shouldRestoreScroll])

  const handleSymbolClick = (clickedSymbol: string) => {
    navigate(`/stock/${clickedSymbol}`)
  }

  // Flatten all pages into a single array
  const articles: NewsArticle[] = data?.pages.flatMap((page) =>
    Array.isArray(page?.items) ? page.items.filter((item): item is NewsArticle =>
      item != null && typeof item === 'object' && 'id' in item
    ) : []
  ) ?? []

  // Build navigation context from loaded articles (memoized)
  const navigationList = useMemo(() =>
    articles.map(a => ({ id: a.id, title: a.title })),
    [articles]
  )

  if (isLoading) {
    return (
      <div className={cn('flex items-center justify-center p-12', className)}>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (isError) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-12', className)}>
        <AlertCircle className="h-8 w-8 text-destructive" />
        <p className="text-sm text-muted-foreground">
          {error instanceof Error ? error.message : t('news.noNews')}
        </p>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          {t('common:actions.retry', 'Try again')}
        </Button>
      </div>
    )
  }

  const hasActiveFilters = !!(filters?.search || filters?.sentimentTag || filters?.market)

  if (articles.length === 0) {
    return (
      <div className={cn('flex flex-col items-center justify-center gap-2 p-12', className)}>
        <Newspaper className="h-12 w-12 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">
          {hasActiveFilters
            ? t('news.filter.noResults')
            : symbol
              ? t('news.noNewsForSymbol', { symbol })
              : t('news.noNews')}
        </p>
      </div>
    )
  }

  const listContent = (
    <div className={cn(compact ? 'space-y-1' : '')}>
      {articles.map((article, index) => (
        <NewsCard
          key={article.id}
          article={article}
          compact={compact}
          onSymbolClick={handleSymbolClick}
          {...(!compact ? { navigationContext: { articles: navigationList, currentIndex: index } } : {})}
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
  )

  // If maxHeight is set (embedded contexts like StockDetailPage), use scroll container
  if (maxHeight) {
    return (
      <div
        ref={containerRef}
        style={{ maxHeight }}
        className={cn('overflow-y-auto pr-2 scrollbar-thin scrollbar-thumb-border scrollbar-track-transparent', className)}
      >
        {listContent}
      </div>
    )
  }

  if (isMobile) {
    return (
      <div className={className}>
        <PullToRefresh onRefresh={async () => { await refetch() }} disabled={isLoading}>
          {listContent}
        </PullToRefresh>
      </div>
    )
  }

  return <div className={className}>{listContent}</div>
}
