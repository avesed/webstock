import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NewsArticle } from '@/types'

interface ArticleScoreBadgeProps {
  article: NewsArticle
}

export default function ArticleScoreBadge({ article }: ArticleScoreBadgeProps) {
  const { t } = useTranslation('dashboard')
  const [isExpanded, setIsExpanded] = useState(false)

  const score = article.contentScore
  const details = article.scoreDetails
  const dims = details?.dimensionScores
  const isCritical = details?.isCriticalEvent
  const path = article.processingPath

  if (score == null && !path) return null

  const scoreColor = isCritical
    ? 'bg-red-500/15 text-red-500 border-red-500/30'
    : (score ?? 0) >= 195
      ? 'bg-green-500/15 text-green-500 border-green-500/30'
      : (score ?? 0) >= 105
        ? 'bg-yellow-500/15 text-yellow-500 border-yellow-500/30'
        : 'bg-muted text-muted-foreground border-border'

  return (
    <div className="inline-flex flex-col">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className={cn(
          'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-semibold tabular-nums transition-colors hover:opacity-80',
          scoreColor,
        )}
      >
        {isCritical && <Zap className="h-3 w-3" />}
        {score != null ? `${score}/300` : t('news.statusPending')}
        {path && (
          <span className="text-[10px] font-normal opacity-70">
            {path === 'full_analysis' ? t('news.pathFull') : t('news.pathLite')}
          </span>
        )}
        <ChevronDown className={cn('h-3 w-3 transition-transform', isExpanded && 'rotate-180')} />
      </button>

      {isExpanded && (
        <div className="mt-2 p-3 rounded-lg border bg-muted/30 text-sm space-y-2 max-w-xs">
          {dims && (
            <div className="grid grid-cols-3 gap-3 text-center">
              <div>
                <div className="text-xs text-muted-foreground">{t('news.dimMacro')}</div>
                <div className="font-semibold tabular-nums">{dims.macro ?? '-'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">{t('news.dimMarket')}</div>
                <div className="font-semibold tabular-nums">{dims.market ?? '-'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">{t('news.dimSignal')}</div>
                <div className="font-semibold tabular-nums">{dims.signal ?? '-'}</div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
