import { useEffect, useRef } from 'react'
import { Bot, X, AlertCircle, MessageSquareText } from 'lucide-react'

import { cn } from '@/lib/utils'
import { ChatMessageBubble } from './ChatMessageBubble'
import { ChatInput } from './ChatInput'
import { RagSourcesBadge } from './RagSourcesBadge'
import { ToolCallIndicator } from './ToolCallIndicator'
import type { ChatMessage, ToolCallStatus } from '@/types'

interface StockChatPanelProps {
  readonly symbol: string
  readonly messages: ChatMessage[]
  readonly isStreaming: boolean
  readonly streamingContent: string
  readonly ragSources: Array<Record<string, unknown>>
  readonly activeToolCalls?: ToolCallStatus[]
  readonly error: string | null
  readonly isLoading: boolean
  readonly onSend: (content: string) => void
  readonly onCancel: () => void
  readonly onClose: () => void
  readonly onClearError: () => void
}

export function StockChatPanel({
  symbol,
  messages,
  isStreaming,
  streamingContent,
  ragSources,
  activeToolCalls,
  error,
  isLoading,
  onSend,
  onCancel,
  onClose,
  onClearError,
}: StockChatPanelProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages or streaming content
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamingContent, isStreaming, activeToolCalls])

  // Build the streaming message object for rendering
  const streamingMessage: ChatMessage | null =
    isStreaming && streamingContent
      ? {
          id: 'streaming',
          conversationId: '',
          role: 'assistant',
          content: streamingContent,
          tokenCount: null,
          model: null,
          toolCalls: null,
          ragContext: null,
          createdAt: new Date().toISOString(),
        }
      : null

  return (
    <div
      role="dialog"
      aria-label={`Chat about ${symbol}`}
      className={cn(
        'flex flex-col overflow-hidden rounded-2xl border bg-card shadow-2xl'
      )}
    >
      {/* Header bar */}
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <span className="inline-flex items-center rounded-md bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
          {symbol}
        </span>
        <div className="flex items-center gap-1.5 text-sm font-medium">
          <MessageSquareText className="h-4 w-4 text-muted-foreground" />
          <span>AI Chat</span>
        </div>
        <div className="flex-1" />
        <button
          type="button"
          aria-label="Close chat"
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Error banner */}
      {error && (
        <div role="alert" className="flex items-center gap-2 border-b bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            className="text-xs font-medium underline hover:no-underline"
            onClick={onClearError}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Scrollable messages area */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto" aria-live="polite">
        <div className="px-4 py-4 space-y-4">
          {/* Loading spinner */}
          {isLoading && messages.length === 0 && (
            <div className="flex items-center justify-center py-12">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}

          {/* Empty state */}
          {!isLoading && messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <Bot className="h-10 w-10 text-muted-foreground/40 mb-3" />
              <p className="text-sm text-muted-foreground">
                Send a message to start chatting about {symbol}
              </p>
            </div>
          )}

          {/* Message list */}
          {messages.map((message) => (
            <ChatMessageBubble key={message.id} message={message} />
          ))}

          {/* RAG sources shown before streaming/last assistant message */}
          {ragSources.length > 0 && (
            <RagSourcesBadge sources={ragSources} />
          )}

          {/* Tool call indicators */}
          {activeToolCalls && activeToolCalls.length > 0 && (
            <ToolCallIndicator toolCalls={activeToolCalls} />
          )}

          {/* Streaming message */}
          {streamingMessage && (
            <ChatMessageBubble message={streamingMessage} isStreaming />
          )}

          {/* Streaming indicator when no content yet (bounce dots) */}
          {isStreaming && !streamingContent && (
            <div className="flex gap-3">
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
                <Bot className="h-4 w-4 text-muted-foreground" />
              </div>
              <div className="flex items-center rounded-2xl rounded-bl-md bg-muted px-4 py-2.5">
                <div className="flex gap-1">
                  <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40 [animation-delay:0ms]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40 [animation-delay:150ms]" />
                  <span className="h-2 w-2 animate-bounce rounded-full bg-muted-foreground/40 [animation-delay:300ms]" />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input */}
      <ChatInput onSend={onSend} isStreaming={isStreaming} onCancel={onCancel} />
    </div>
  )
}
