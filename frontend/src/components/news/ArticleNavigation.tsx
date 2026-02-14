import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { decodeHtmlEntities } from '@/lib/utils'
import type { NewsNavigationContext } from '@/types'

interface ArticleNavigationProps {
  navigation: NewsNavigationContext
  origin?: string
}

export default function ArticleNavigation({ navigation, origin }: ArticleNavigationProps) {
  const { t } = useTranslation('dashboard')
  const { articles, currentIndex } = navigation

  const prevArticle = currentIndex > 0 ? articles[currentIndex - 1] : undefined
  const nextArticle = currentIndex < articles.length - 1 ? articles[currentIndex + 1] : undefined

  if (!prevArticle && !nextArticle) return null

  return (
    <nav className="grid grid-cols-2 gap-4 pt-6 border-t">
      <div>
        {prevArticle && (
          <Link
            to={`/news/${prevArticle.id}`}
            state={{ navigation: { articles, currentIndex: currentIndex - 1 }, origin }}
            className="group flex flex-col gap-1 p-3 -ml-3 rounded-lg hover:bg-accent/50 transition-colors"
          >
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <ChevronLeft className="h-3 w-3" />
              {t('news.reader.previousArticle')}
            </span>
            <span className="text-sm font-medium line-clamp-2 group-hover:text-primary transition-colors">
              {decodeHtmlEntities(prevArticle.title)}
            </span>
          </Link>
        )}
      </div>
      <div className="text-right">
        {nextArticle && (
          <Link
            to={`/news/${nextArticle.id}`}
            state={{ navigation: { articles, currentIndex: currentIndex + 1 }, origin }}
            className="group flex flex-col gap-1 p-3 -mr-3 rounded-lg hover:bg-accent/50 transition-colors text-right"
          >
            <span className="text-xs text-muted-foreground flex items-center gap-1 justify-end">
              {t('news.reader.nextArticle')}
              <ChevronRight className="h-3 w-3" />
            </span>
            <span className="text-sm font-medium line-clamp-2 group-hover:text-primary transition-colors">
              {decodeHtmlEntities(nextArticle.title)}
            </span>
          </Link>
        )}
      </div>
    </nav>
  )
}
