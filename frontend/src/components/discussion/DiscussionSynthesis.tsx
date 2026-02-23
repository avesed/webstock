import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import { FileText, MessageCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'

const remarkPlugins = [remarkGfm]

interface DiscussionSynthesisProps {
  report: string
  onContinueChat: () => void
  isStreaming?: boolean
}

export function DiscussionSynthesis({ report, onContinueChat, isStreaming }: DiscussionSynthesisProps) {
  const { t } = useTranslation('common')
  const memoReport = useMemo(() => report, [report])

  return (
    <div className="rounded-xl border border-primary/20 bg-primary/[0.03]">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-primary/10 px-4 py-2.5">
        <FileText className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-primary">
          {t('discussion.agents.synthesis')}
        </span>
        {isStreaming && (
          <span className="text-[10px] text-muted-foreground/60 animate-pulse">...</span>
        )}
      </div>

      {/* Report content */}
      <div className="px-4 py-3">
        <div
          className="prose prose-sm dark:prose-invert max-w-none min-w-0 break-words [overflow-wrap:anywhere]
            prose-p:mb-2 prose-p:last:mb-0 prose-p:leading-relaxed
            prose-headings:mb-2 prose-headings:font-bold
            prose-li:mb-1
            prose-pre:max-w-full prose-pre:overflow-x-auto
            [&_ul]:my-1 [&_ol]:my-1"
          aria-live={isStreaming ? 'polite' : undefined}
        >
          <ReactMarkdown remarkPlugins={remarkPlugins}>
            {memoReport}
          </ReactMarkdown>
          {isStreaming && (
            <span className="inline-block h-4 w-1.5 animate-pulse bg-primary/70 ml-0.5 align-text-bottom rounded-sm" />
          )}
        </div>
      </div>

      {/* Continue chatting footer */}
      {!isStreaming && (
        <div className="border-t border-primary/10 px-4 py-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted-foreground">
              {t('discussion.continueChatDesc')}
            </p>
            <Button size="sm" onClick={onContinueChat} className="w-full gap-2 sm:w-auto">
              <MessageCircle className="h-4 w-4" />
              {t('discussion.continueChat')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
