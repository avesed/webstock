import { useState } from 'react'
import { BookOpen, ChevronRight } from 'lucide-react'

import { cn } from '@/lib/utils'

interface RagSourcesBadgeProps {
  readonly sources: Array<Record<string, unknown>>
}

export function RagSourcesBadge({ sources }: RagSourcesBadgeProps) {
  const [isExpanded, setIsExpanded] = useState(false)

  if (sources.length === 0) return null

  return (
    <div className="mx-11 mb-2">
      <button
        type="button"
        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <BookOpen className="h-3 w-3" />
        <span>{sources.length} source{sources.length !== 1 ? 's' : ''} referenced</span>
        <ChevronRight
          className={cn(
            'h-3 w-3 transition-transform',
            isExpanded && 'rotate-90'
          )}
        />
      </button>

      {isExpanded && (
        <div className="mt-1.5 space-y-1 rounded-md border bg-muted/30 p-2">
          {sources.map((source, index) => {
            const type = String(source.type ?? source.source_type ?? 'document')
            const symbol = source.symbol ? String(source.symbol) : null
            const title = source.title ? String(source.title) : null
            const name = source.name ? String(source.name) : null

            return (
              <div
                key={index}
                className="flex items-center gap-2 text-xs text-muted-foreground"
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-primary/10 text-[9px] font-medium text-primary">
                  {index + 1}
                </span>
                <span className="capitalize font-medium">{type}</span>
                {symbol && (
                  <span className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                    {symbol}
                  </span>
                )}
                {(title ?? name) && (
                  <span className="truncate">{title ?? name}</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
