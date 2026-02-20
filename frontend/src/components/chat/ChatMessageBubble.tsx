import { memo, useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Bot, User } from 'lucide-react'

import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/utils'
import type { ChatMessage } from '@/types'

interface ChatMessageBubbleProps {
  readonly message: ChatMessage
  readonly isStreaming?: boolean
}

/**
 * Custom renderers for ReactMarkdown inside chat bubbles.
 * Keeps code blocks styled with a language label and ensures
 * tables, inline code, etc. fit the chat aesthetic.
 */
const markdownComponents: React.ComponentProps<typeof ReactMarkdown>['components'] = {
  // Code blocks with optional language label
  pre({ children }) {
    return <div className="mb-2 last:mb-0">{children}</div>
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
    // Inline code
    return (
      <code className="rounded bg-background/40 px-1 py-0.5 font-mono text-xs" {...props}>
        {children}
      </code>
    )
  },
  // Tables
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

const remarkPlugins = [remarkGfm]

const AssistantContent = memo(function AssistantContent({
  content,
  isStreaming,
}: {
  content: string
  isStreaming: boolean
}) {
  const memoizedContent = useMemo(() => content, [content])
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none min-w-0 break-words
      prose-p:mb-2 prose-p:last:mb-0 prose-p:leading-relaxed
      prose-headings:mb-2 prose-headings:font-bold
      prose-li:mb-1
      prose-pre:bg-transparent prose-pre:p-0
      [&_ul]:my-1 [&_ol]:my-1"
    >
      <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>
        {memoizedContent}
      </ReactMarkdown>
      {isStreaming && (
        <span className="inline-block h-4 w-1.5 animate-pulse bg-foreground/70 ml-0.5 align-text-bottom rounded-sm" />
      )}
    </div>
  )
})

export function ChatMessageBubble({ message, isStreaming = false }: ChatMessageBubbleProps) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {/* Assistant avatar */}
      {!isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
          <Bot className="h-4 w-4 text-muted-foreground" />
        </div>
      )}

      <div className={cn('flex max-w-[80%] flex-col', isUser ? 'items-end' : 'items-start')}>
        {/* Message bubble */}
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm',
            isUser
              ? 'bg-primary text-primary-foreground rounded-br-md'
              : 'bg-muted rounded-bl-md'
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          ) : (
            <AssistantContent content={message.content} isStreaming={isStreaming} />
          )}
        </div>

        {/* Timestamp */}
        <span className="mt-1 text-[10px] text-muted-foreground/60 px-1">
          {formatRelativeTime(message.createdAt)}
        </span>
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}
