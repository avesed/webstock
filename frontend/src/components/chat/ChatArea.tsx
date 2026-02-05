import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Bot, AlertCircle } from 'lucide-react'

import { useChatStore } from '@/stores/chatStore'
import { ChatMessageBubble } from './ChatMessageBubble'
import { ChatInput } from './ChatInput'
import { RagSourcesBadge } from './RagSourcesBadge'
import { ToolCallIndicator } from './ToolCallIndicator'
import type { ChatMessage } from '@/types'

export function ChatArea() {
  const { t } = useTranslation('chat')
  const currentConversationId = useChatStore((s) => s.currentConversationId)
  const messages = useChatStore((s) => s.messages)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const streamingContent = useChatStore((s) => s.streamingContent)
  const ragSources = useChatStore((s) => s.ragSources)
  const activeToolCalls = useChatStore((s) => s.activeToolCalls)
  const error = useChatStore((s) => s.error)
  const isLoadingMessages = useChatStore((s) => s.isLoadingMessages)
  const sendMessage = useChatStore((s) => s.sendMessage)
  const cancelStream = useChatStore((s) => s.cancelStream)
  const clearError = useChatStore((s) => s.clearError)

  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new messages or streaming content
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [messages, streamingContent, isStreaming, activeToolCalls])

  const handleSend = (content: string) => {
    sendMessage(content)
  }

  // No conversation selected
  if (!currentConversationId) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center p-8">
        <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-muted mb-4">
          <Bot className="h-8 w-8 text-muted-foreground" />
        </div>
        <h3 className="text-lg font-semibold mb-1">{t('title')}</h3>
        <p className="text-sm text-muted-foreground max-w-sm">
          {t('empty.description')}
        </p>
      </div>
    )
  }

  // Build the streaming message object for rendering
  const streamingMessage: ChatMessage | null =
    isStreaming && streamingContent
      ? {
          id: 'streaming',
          conversationId: currentConversationId,
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
    <div className="flex h-full flex-col">
      {/* Error banner */}
      {error && (
        <div className="flex items-center gap-2 border-b bg-destructive/10 px-4 py-2 text-sm text-destructive">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span className="flex-1">{error}</span>
          <button
            type="button"
            className="text-xs font-medium underline hover:no-underline"
            onClick={clearError}
          >
            {t('common:actions.close', 'Dismiss')}
          </button>
        </div>
      )}

      {/* Message list */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6 space-y-4">
          {isLoadingMessages && messages.length === 0 && (
            <div className="flex items-center justify-center py-12">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          )}

          {!isLoadingMessages && messages.length === 0 && !isStreaming && (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <Bot className="h-10 w-10 text-muted-foreground/40 mb-3" />
              <p className="text-sm text-muted-foreground">
                {t('empty.title')}
              </p>
            </div>
          )}

          {messages.map((message) => (
            <ChatMessageBubble key={message.id} message={message} />
          ))}

          {/* RAG sources shown before streaming/last assistant message */}
          {ragSources.length > 0 && (
            <RagSourcesBadge sources={ragSources} />
          )}

          {/* Tool call indicators */}
          {activeToolCalls.length > 0 && (
            <ToolCallIndicator toolCalls={activeToolCalls} />
          )}

          {/* Streaming message */}
          {streamingMessage && (
            <ChatMessageBubble message={streamingMessage} isStreaming />
          )}

          {/* Streaming indicator when no content yet */}
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
      <ChatInput onSend={handleSend} isStreaming={isStreaming} onCancel={cancelStream} />
    </div>
  )
}
