import { useState, useRef, useCallback, useEffect } from 'react'
import { MessageSquare, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { chatApi } from '@/api'
import { getErrorMessage } from '@/api/client'
import { useLocale } from '@/hooks/useLocale'
import { StockChatPanel } from './StockChatPanel'
import {
  useIsInStockChatProvider,
  useStockChatState,
  useStockChatActions,
} from '@/components/stock'
import type { ChatMessage, ChatStreamEvent, ToolCallStatus } from '@/types'

interface StockChatWidgetProps {
  readonly symbol: string
}

const LOG_PREFIX = '[StockChatWidget]'

/** Generate a UUID, with fallback for non-secure contexts (HTTP over LAN). */
function generateUUID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    const v = c === 'x' ? r : (r & 0x3) | 0x8
    return v.toString(16)
  })
}

/**
 * Floating chat widget for stock-specific conversations.
 *
 * When inside StockChatProvider (on StockDetailPage), syncs with AI Tab chat.
 * When outside (standalone), manages its own local state.
 */
export function StockChatWidget({ symbol }: StockChatWidgetProps) {
  const isInProvider = useIsInStockChatProvider()

  if (isInProvider) {
    return <StockChatWidgetWithContext symbol={symbol} />
  }

  return <StockChatWidgetStandalone symbol={symbol} />
}

/**
 * Widget implementation that consumes shared Context state.
 * Used when rendered inside StockChatProvider.
 */
function StockChatWidgetWithContext({ symbol }: StockChatWidgetProps) {
  const [isOpen, setIsOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // Consume shared state from Context
  const {
    messages,
    isStreaming,
    streamingContent,
    ragSources,
    activeToolCalls,
    error,
    isLoading,
  } = useStockChatState()

  const { sendMessage, cancelStream, clearError } = useStockChatActions()

  // Toggle widget
  const handleToggle = useCallback(() => {
    setIsOpen((prev) => !prev)
  }, [])

  // Close panel
  const handleClose = useCallback(() => {
    setIsOpen(false)
    buttonRef.current?.focus()
  }, [])

  // Focus management
  useEffect(() => {
    if (isOpen && panelRef.current) {
      panelRef.current.focus()
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, handleClose])

  return (
    <>
      {/* Panel */}
      {isOpen && (
        <div
          ref={panelRef}
          tabIndex={-1}
          className="fixed bottom-24 right-6 z-40 flex w-96 max-w-[calc(100vw-2rem)] flex-col overflow-hidden outline-none animate-in slide-in-from-bottom-4 fade-in duration-200"
          style={{ height: '500px', maxHeight: 'calc(100dvh - 10rem)' }}
        >
          <StockChatPanel
            symbol={symbol}
            messages={messages}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            ragSources={ragSources}
            activeToolCalls={activeToolCalls}
            error={error}
            isLoading={isLoading}
            onSend={sendMessage}
            onCancel={cancelStream}
            onClose={handleClose}
            onClearError={clearError}
          />
        </div>
      )}

      {/* Floating button */}
      <button
        ref={buttonRef}
        type="button"
        onClick={handleToggle}
        className={cn(
          'fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full shadow-lg transition-all duration-200 hover:scale-105 hover:shadow-xl',
          'bg-primary text-primary-foreground',
        )}
        aria-label={isOpen ? 'Close chat' : 'Open chat'}
        aria-expanded={isOpen}
      >
        {isOpen ? (
          <X className="h-6 w-6" />
        ) : (
          <MessageSquare className="h-6 w-6" />
        )}
      </button>
    </>
  )
}

/**
 * Standalone widget implementation with local state.
 * Used when rendered outside StockChatProvider (backward compatibility).
 */
function StockChatWidgetStandalone({ symbol }: StockChatWidgetProps) {
  const { locale } = useLocale()
  const [isOpen, setIsOpen] = useState(false)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [ragSources, setRagSources] = useState<Array<Record<string, unknown>>>([])
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCallStatus[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const abortControllerRef = useRef<AbortController | null>(null)
  const navigationRef = useRef(0)
  const streamFinalizedRef = useRef(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  /** Commit any accumulated streaming content as a partial assistant message. */
  const commitPartialStream = useCallback((convId: string) => {
    setStreamingContent((prev) => {
      if (prev) {
        const partialMessage: ChatMessage = {
          id: `msg-${generateUUID()}`,
          conversationId: convId,
          role: 'assistant',
          content: prev,
          tokenCount: null,
          model: null,
          toolCalls: null,
          ragContext: null,
          createdAt: new Date().toISOString(),
        }
        setMessages((msgs) => [...msgs, partialMessage])
      }
      return ''
    })
  }, [])

  /** Abort the current stream. */
  const abortStream = useCallback(() => {
    abortControllerRef.current?.abort()
  }, [])

  // ---------------------------------------------------------------------------
  // Conversation Resolution
  // ---------------------------------------------------------------------------

  const resolveConversation = useCallback(async () => {
    const version = ++navigationRef.current

    abortStream()

    setIsLoading(true)
    setError(null)
    setMessages([])
    setStreamingContent('')
    setRagSources([])
    setActiveToolCalls([])
    setIsStreaming(false)

    try {
      const result = await chatApi.listConversations(50)
      if (navigationRef.current !== version) {
        console.warn(LOG_PREFIX, 'Stale conversation resolution discarded for', symbol)
        return
      }

      if (result.total > 50) {
        console.warn(LOG_PREFIX, `Only searched first 50 of ${result.total} conversations for symbol match`)
      }

      const existing = result.conversations.find(
        (c) => c.symbol === symbol && !c.isArchived,
      )

      let convId: string
      if (existing) {
        convId = existing.id
        const msgs = await chatApi.getMessages(existing.id)
        if (navigationRef.current !== version) {
          console.warn(LOG_PREFIX, 'Stale message load discarded for', symbol)
          return
        }
        setMessages(msgs)
      } else {
        const newConv = await chatApi.createConversation(
          `${symbol} Chat`,
          symbol,
        )
        if (navigationRef.current !== version) {
          console.warn(LOG_PREFIX, 'Stale conversation create discarded for', symbol)
          return
        }
        convId = newConv.id
      }

      setConversationId(convId)
    } catch (err) {
      if (navigationRef.current !== version) return
      console.error(LOG_PREFIX, 'Conversation resolution failed:', err)
      setError(getErrorMessage(err))
    } finally {
      if (navigationRef.current === version) {
        setIsLoading(false)
      }
    }
  }, [symbol, abortStream])

  useEffect(() => {
    if (isOpen) {
      resolveConversation()
    }
  }, [isOpen, symbol, resolveConversation])

  // ---------------------------------------------------------------------------
  // Send Message
  // ---------------------------------------------------------------------------

  const handleSend = useCallback(
    (content: string) => {
      if (!conversationId) {
        setError('No active conversation. Please wait for loading to complete.')
        return
      }
      if (isStreaming) return

      streamFinalizedRef.current = false

      const userMessage: ChatMessage = {
        id: `temp-${generateUUID()}`,
        conversationId,
        role: 'user',
        content,
        tokenCount: null,
        model: null,
        toolCalls: null,
        ragContext: null,
        createdAt: new Date().toISOString(),
      }

      setMessages((prev) => [...prev, userMessage])
      setIsStreaming(true)
      setStreamingContent('')
      setRagSources([])
      setActiveToolCalls([])
      setError(null)

      const capturedConvId = conversationId

      const onEvent = (event: ChatStreamEvent) => {
        switch (event.type) {
          case 'content_delta':
            if (event.content) {
              setStreamingContent((prev) => prev + event.content)
            }
            break
          case 'rag_sources':
            if (event.sources) {
              setRagSources(event.sources)
            }
            break
          case 'tool_call_start':
            if (event.toolCallId && event.toolName) {
              setActiveToolCalls((prev) => [
                ...prev,
                {
                  id: event.toolCallId!,
                  name: event.toolName!,
                  label: event.toolLabel ?? event.toolName!,
                  status: 'running' as const,
                },
              ])
            }
            break
          case 'tool_call_result':
            if (event.toolCallId) {
              setActiveToolCalls((prev) =>
                prev.map((tc) =>
                  tc.id === event.toolCallId
                    ? { ...tc, status: event.success ? 'completed' as const : 'failed' as const }
                    : tc
                ),
              )
            }
            break
          case 'message_end': {
            if (streamFinalizedRef.current) break
            streamFinalizedRef.current = true

            setStreamingContent((prev) => {
              const assistantMessage: ChatMessage = {
                id: event.messageId ?? `msg-${generateUUID()}`,
                conversationId: capturedConvId,
                role: 'assistant',
                content: prev,
                tokenCount: event.tokenCount ?? null,
                model: event.model ?? null,
                toolCalls: null,
                ragContext: null,
                createdAt: new Date().toISOString(),
              }
              setMessages((msgs) => [...msgs, assistantMessage])
              return ''
            })
            setIsStreaming(false)
            setActiveToolCalls([])
            break
          }
          case 'error':
            console.warn(LOG_PREFIX, 'Server-sent stream error:', event.error)
            setError(event.error ?? 'An error occurred')
            setIsStreaming(false)
            setActiveToolCalls([])
            break
          case 'timeout':
            console.warn(LOG_PREFIX, 'Stream timeout from server')
            setError('Response timed out. Please try again.')
            setIsStreaming(false)
            setActiveToolCalls([])
            break
        }
      }

      const onError = (err: unknown) => {
        if (abortControllerRef.current?.signal.aborted) return
        console.error(LOG_PREFIX, 'Stream error:', err)
        setError(getErrorMessage(err))
        setIsStreaming(false)
        setActiveToolCalls([])
      }

      const onDone = () => {
        if (streamFinalizedRef.current) return
        streamFinalizedRef.current = true

        console.warn(LOG_PREFIX, 'Stream ended without message_end event')
        setStreamingContent((prev) => {
          if (prev) {
            const assistantMessage: ChatMessage = {
              id: `msg-${generateUUID()}`,
              conversationId: capturedConvId,
              role: 'assistant',
              content: prev,
              tokenCount: null,
              model: null,
              toolCalls: null,
              ragContext: null,
              createdAt: new Date().toISOString(),
            }
            setMessages((msgs) => [...msgs, assistantMessage])
          }
          return ''
        })
        setIsStreaming(false)
        setActiveToolCalls([])
      }

      abortControllerRef.current = chatApi.streamMessage(
        conversationId,
        content,
        symbol,
        locale,
        onEvent,
        onError,
        onDone,
      )
    },
    [conversationId, isStreaming, symbol, locale],
  )

  // ---------------------------------------------------------------------------
  // Cancel Stream
  // ---------------------------------------------------------------------------

  const handleCancel = useCallback(() => {
    if (conversationId) {
      commitPartialStream(conversationId)
    }
    abortStream()
    setIsStreaming(false)
    setActiveToolCalls([])
  }, [conversationId, commitPartialStream, abortStream])

  // ---------------------------------------------------------------------------
  // Close Panel
  // ---------------------------------------------------------------------------

  const handleClose = useCallback(() => {
    if (isStreaming && conversationId) {
      commitPartialStream(conversationId)
    }
    abortStream()
    setIsStreaming(false)
    setActiveToolCalls([])
    setIsOpen(false)
    buttonRef.current?.focus()
  }, [isStreaming, conversationId, commitPartialStream, abortStream])

  // ---------------------------------------------------------------------------
  // Cleanup on Unmount
  // ---------------------------------------------------------------------------

  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Toggle
  // ---------------------------------------------------------------------------

  const handleToggle = useCallback(() => {
    if (isOpen) {
      handleClose()
    } else {
      setIsOpen(true)
    }
  }, [isOpen, handleClose])

  // ---------------------------------------------------------------------------
  // Focus Management
  // ---------------------------------------------------------------------------

  useEffect(() => {
    if (isOpen && panelRef.current) {
      panelRef.current.focus()
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, handleClose])

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <>
      {/* Panel */}
      {isOpen && (
        <div
          ref={panelRef}
          tabIndex={-1}
          className="fixed bottom-24 right-6 z-40 flex w-96 max-w-[calc(100vw-2rem)] flex-col overflow-hidden outline-none animate-in slide-in-from-bottom-4 fade-in duration-200"
          style={{ height: '500px', maxHeight: 'calc(100dvh - 10rem)' }}
        >
          <StockChatPanel
            symbol={symbol}
            messages={messages}
            isStreaming={isStreaming}
            streamingContent={streamingContent}
            ragSources={ragSources}
            activeToolCalls={activeToolCalls}
            error={error}
            isLoading={isLoading}
            onSend={handleSend}
            onCancel={handleCancel}
            onClose={handleClose}
            onClearError={() => setError(null)}
          />
        </div>
      )}

      {/* Floating button */}
      <button
        ref={buttonRef}
        type="button"
        onClick={handleToggle}
        className={cn(
          'fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full shadow-lg transition-all duration-200 hover:scale-105 hover:shadow-xl',
          'bg-primary text-primary-foreground',
        )}
        aria-label={isOpen ? 'Close chat' : 'Open chat'}
        aria-expanded={isOpen}
      >
        {isOpen ? (
          <X className="h-6 w-6" />
        ) : (
          <MessageSquare className="h-6 w-6" />
        )}
      </button>
    </>
  )
}
