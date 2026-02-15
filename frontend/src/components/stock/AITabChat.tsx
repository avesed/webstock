import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, AlertCircle, MessageSquareText, SquarePen } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ChatMessageBubble } from '@/components/chat/ChatMessageBubble'
import { ChatInput } from '@/components/chat/ChatInput'
import { RagSourcesBadge } from '@/components/chat/RagSourcesBadge'
import { ToolCallIndicator } from '@/components/chat/ToolCallIndicator'
import { useStockChatState, useStockChatActions } from './StockChatContext'
import type { ChatMessage } from '@/types'

/**
 * AI Tab embedded chat panel.
 * Consumes shared state from StockChatContext to sync with StockChatWidget.
 */
export function AITabChat() {
  const { t } = useTranslation('chat')
  const {
    messages,
    isStreaming,
    streamingContent,
    ragSources,
    activeToolCalls,
    error,
    isLoading,
  } = useStockChatState()

  const { sendMessage, cancelStream, clearError, startNewChat } = useStockChatActions()

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
    <Card className="flex flex-col" style={{ height: '500px' }}>
      <CardHeader className="pb-2 border-b shrink-0">
        <div className="flex items-center gap-2">
          <MessageSquareText className="h-4 w-4 text-muted-foreground" />
          <CardTitle className="text-base">{t('title', 'AI Chat')}</CardTitle>
          <div className="flex-1" />
          <button
            type="button"
            aria-label={t('widget.newChat', 'New Chat')}
            className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            onClick={startNewChat}
          >
            <SquarePen className="h-4 w-4" />
          </button>
        </div>
      </CardHeader>

      {/* Error banner */}
      {error && (
        <div role="alert" className="flex items-center gap-2 border-b bg-destructive/10 px-4 py-2 text-sm text-destructive shrink-0">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            className="text-xs font-medium underline hover:no-underline"
            onClick={clearError}
          >
            {t('dismiss', 'Dismiss')}
          </button>
        </div>
      )}

      {/* Messages area */}
      <CardContent ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
        {isLoading && messages.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
          </div>
        )}

        {!isLoading && messages.length === 0 && !isStreaming && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Bot className="h-10 w-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">
              {t('emptyState', 'Send a message to start the conversation')}
            </p>
          </div>
        )}

        {messages.map((message) => (
          <ChatMessageBubble key={message.id} message={message} />
        ))}

        {ragSources.length > 0 && <RagSourcesBadge sources={ragSources} />}

        {activeToolCalls && activeToolCalls.length > 0 && (
          <ToolCallIndicator toolCalls={activeToolCalls} />
        )}

        {streamingMessage && (
          <ChatMessageBubble message={streamingMessage} isStreaming />
        )}

        {/* Loading indicator when streaming but no content yet */}
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
      </CardContent>

      {/* Input */}
      <div className="border-t shrink-0">
        <ChatInput
          onSend={sendMessage}
          isStreaming={isStreaming}
          onCancel={cancelStream}
        />
      </div>
    </Card>
  )
}
