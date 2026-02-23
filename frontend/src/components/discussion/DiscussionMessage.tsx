import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from 'react-i18next'
import type { LucideIcon } from 'lucide-react'
import {
  Brain,
  BarChart3,
  TrendingUp,
  MessageSquare,
  Newspaper,
  Wrench,
} from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'

/**
 * Agent visual config — chat-group style with colored bubbles.
 * Each agent has a distinct accent color for instant visual identification.
 */
const AGENT_CONFIG: Record<string, {
  icon: LucideIcon
  color: string
  avatarBg: string
  bubbleBg: string
  bubbleBorder: string
}> = {
  moderator:   { icon: Brain,         color: 'text-violet-600 dark:text-violet-400',   avatarBg: 'bg-violet-100 dark:bg-violet-900/50',   bubbleBg: 'bg-violet-50/80 dark:bg-violet-950/30',   bubbleBorder: 'border-violet-200/60 dark:border-violet-800/40' },
  fundamental: { icon: BarChart3,     color: 'text-blue-600 dark:text-blue-400',       avatarBg: 'bg-blue-100 dark:bg-blue-900/50',       bubbleBg: 'bg-blue-50/80 dark:bg-blue-950/30',       bubbleBorder: 'border-blue-200/60 dark:border-blue-800/40' },
  technical:   { icon: TrendingUp,    color: 'text-emerald-600 dark:text-emerald-400', avatarBg: 'bg-emerald-100 dark:bg-emerald-900/50', bubbleBg: 'bg-emerald-50/80 dark:bg-emerald-950/30', bubbleBorder: 'border-emerald-200/60 dark:border-emerald-800/40' },
  sentiment:   { icon: MessageSquare, color: 'text-amber-600 dark:text-amber-400',     avatarBg: 'bg-amber-100 dark:bg-amber-900/50',     bubbleBg: 'bg-amber-50/80 dark:bg-amber-950/30',     bubbleBorder: 'border-amber-200/60 dark:border-amber-800/40' },
  news:        { icon: Newspaper,     color: 'text-rose-600 dark:text-rose-400',       avatarBg: 'bg-rose-100 dark:bg-rose-900/50',       bubbleBg: 'bg-rose-50/80 dark:bg-rose-950/30',       bubbleBorder: 'border-rose-200/60 dark:border-rose-800/40' },
}

const DEFAULT_AGENT_CONFIG = {
  icon: Brain,
  color: 'text-muted-foreground',
  avatarBg: 'bg-muted',
  bubbleBg: 'bg-muted/50',
  bubbleBorder: 'border-border',
}

const remarkPlugins = [remarkGfm]

/**
 * Custom markdown components — matches ChatMessageBubble for consistency.
 */
const markdownComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  pre({ children }) {
    return <div className="mb-2 overflow-hidden last:mb-0">{children}</div>
  },
  code({ className, children, ...props }) {
    const match = /language-(\w+)/.exec(className ?? '')
    const isBlock = Boolean(match) || (typeof children === 'string' && children.includes('\n'))
    if (isBlock) {
      return (
        <>
          {match?.[1] && (
            <div className="rounded-t-md bg-background/50 px-3 py-1 text-[10px] font-mono text-muted-foreground">
              {match[1]}
            </div>
          )}
          <pre className={cn(
            'overflow-x-auto bg-background/30 p-3 font-mono text-xs leading-relaxed',
            match?.[1] ? 'rounded-b-md' : 'rounded-md'
          )}>
            <code>{children}</code>
          </pre>
        </>
      )
    }
    return (
      <code className="rounded bg-background/40 px-1 py-0.5 font-mono text-xs" {...props}>
        {children}
      </code>
    )
  },
  table({ children }) {
    return (
      <div className="mb-2 overflow-x-auto last:mb-0">
        <table className="min-w-full text-xs">{children}</table>
      </div>
    )
  },
  th({ children }) {
    return (
      <th className="border-b border-foreground/20 px-2 py-1.5 text-left font-semibold whitespace-nowrap">
        {children}
      </th>
    )
  },
  td({ children }) {
    return (
      <td className="border-b border-foreground/10 px-2 py-1 whitespace-nowrap">
        {children}
      </td>
    )
  },
}

interface DiscussionMessageProps {
  agentType: string
  content: string
  isStreaming?: boolean
  toolCalls?: Record<string, unknown>[] | null
}

/**
 * Chat-group style message bubble.
 * Left-aligned with agent avatar, name badge, and colored bubble —
 * resembles a group chat where each agent is a participant.
 */
function DiscussionMessageInner({
  agentType,
  content,
  isStreaming,
  toolCalls,
}: DiscussionMessageProps) {
  const { t } = useTranslation('common')

  const config = AGENT_CONFIG[agentType] ?? DEFAULT_AGENT_CONFIG
  const AgentIcon = config.icon
  const agentName = t(`discussion.agents.${agentType}`, agentType)
  const memoContent = useMemo(() => content, [content])

  return (
    <div className="flex gap-2.5 py-2" role="article" aria-label={agentName}>
      {/* Agent avatar */}
      <div
        className={cn(
          'flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full mt-0.5',
          config.avatarBg
        )}
      >
        <AgentIcon className={cn('h-4 w-4', config.color)} />
      </div>

      {/* Bubble */}
      <div className="min-w-0 flex-1">
        {/* Agent name */}
        <div className="mb-1 flex items-center gap-1.5">
          <span className={cn('text-xs font-semibold', config.color)}>
            {agentName}
          </span>
          {isStreaming && (
            <span className="text-[10px] text-muted-foreground/60 animate-pulse">
              {t('discussion.typing')}
            </span>
          )}
        </div>

        {/* Content bubble */}
        <div
          className={cn(
            'rounded-2xl rounded-tl-md border px-3.5 py-2.5 text-sm',
            config.bubbleBg,
            config.bubbleBorder,
          )}
        >
          <div className="prose prose-sm dark:prose-invert max-w-none min-w-0 break-words [overflow-wrap:anywhere]
            prose-p:mb-2 prose-p:last:mb-0 prose-p:leading-relaxed
            prose-headings:mb-2 prose-headings:font-bold
            prose-li:mb-1
            prose-pre:bg-transparent prose-pre:p-0 prose-pre:max-w-full
            [&_ul]:my-1 [&_ol]:my-1"
          >
            <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
              {memoContent}
            </ReactMarkdown>
            {isStreaming && (
              <span className="inline-block h-4 w-1.5 animate-pulse bg-foreground/70 ml-0.5 align-text-bottom rounded-sm" />
            )}
          </div>

          {/* Tool call badges */}
          {toolCalls && toolCalls.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {toolCalls.map((call, idx) => {
                const toolName = typeof call['name'] === 'string' ? call['name'] : `tool-${idx}`
                return (
                  <Badge
                    key={`${toolName}-${idx}`}
                    variant="outline"
                    className="gap-1 text-[10px] font-normal"
                  >
                    <Wrench className="h-3 w-3" />
                    {toolName}
                  </Badge>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

const DiscussionMessage = memo(DiscussionMessageInner)
DiscussionMessage.displayName = 'DiscussionMessage'

export { DiscussionMessage, AGENT_CONFIG, DEFAULT_AGENT_CONFIG }
export type { DiscussionMessageProps }
