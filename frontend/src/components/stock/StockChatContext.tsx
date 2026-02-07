import {
  createContext,
  useContext,
  useState,
  useRef,
  useCallback,
  useEffect,
  useMemo,
  type ReactNode,
} from 'react'
import { chatApi } from '@/api'
import { getErrorMessage } from '@/api/client'
import { useLocale } from '@/hooks/useLocale'
import type { ChatMessage, ChatStreamEvent, ToolCallStatus } from '@/types'

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------

/**
 * Stock chat state - contains all reactive state values.
 * Split into separate context to avoid re-renders when only actions are needed.
 */
export interface StockChatState {
  conversationId: string | null
  messages: ChatMessage[]
  isStreaming: boolean
  streamingContent: string
  ragSources: Array<Record<string, unknown>>
  activeToolCalls: ToolCallStatus[]
  isLoading: boolean
  error: string | null
}

/**
 * Stock chat actions - contains stable function references.
 * Split into separate context to avoid re-renders from state changes.
 */
export interface StockChatActions {
  sendMessage: (content: string) => void
  cancelStream: () => void
  clearError: () => void
  refreshConversation: () => Promise<void>
}

// -----------------------------------------------------------------------------
// Contexts
// -----------------------------------------------------------------------------

/**
 * Context for stock chat state.
 * Updates frequently during streaming - consumers should use selectively.
 */
const StockChatStateContext = createContext<StockChatState | null>(null)

/**
 * Context for stock chat actions.
 * Stable references - safe to consume without re-render concerns.
 */
const StockChatActionsContext = createContext<StockChatActions | null>(null)

// -----------------------------------------------------------------------------
// Hooks
// -----------------------------------------------------------------------------

/**
 * Access stock chat state. Must be used within StockChatProvider.
 * Note: Components using this will re-render on any state change.
 */
export function useStockChatState(): StockChatState {
  const state = useContext(StockChatStateContext)
  if (!state) {
    throw new Error('useStockChatState must be used within StockChatProvider')
  }
  return state
}

/**
 * Access stock chat actions. Must be used within StockChatProvider.
 * Actions are stable references and won't cause re-renders.
 */
export function useStockChatActions(): StockChatActions {
  const actions = useContext(StockChatActionsContext)
  if (!actions) {
    throw new Error('useStockChatActions must be used within StockChatProvider')
  }
  return actions
}

/**
 * Convenience hook to access both state and actions.
 * Use useStockChatActions() alone if you only need actions to avoid re-renders.
 */
export function useStockChatContext(): StockChatState & StockChatActions {
  const state = useStockChatState()
  const actions = useStockChatActions()
  return { ...state, ...actions }
}

/**
 * Check if currently within a StockChatProvider.
 * Useful for conditional context consumption (e.g., Widget fallback).
 */
export function useIsInStockChatProvider(): boolean {
  return useContext(StockChatStateContext) !== null
}

// -----------------------------------------------------------------------------
// Helper
// -----------------------------------------------------------------------------

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

// -----------------------------------------------------------------------------
// Provider
// -----------------------------------------------------------------------------

interface StockChatProviderProps {
  symbol: string
  children: ReactNode
}

const LOG_PREFIX = '[StockChatContext]'

/**
 * Provider for stock-specific chat state.
 *
 * Manages conversation resolution, message streaming, and state synchronization
 * between StockChatWidget and AITabChat components.
 *
 * Key features:
 * - Automatic conversation resolution on mount
 * - Stream race condition protection (navigationRef, streamFinalizedRef)
 * - Partial content commit on cancel/close
 * - Split State/Actions contexts for render optimization
 */
export function StockChatProvider({ symbol, children }: StockChatProviderProps) {
  const { locale } = useLocale()

  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------

  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [ragSources, setRagSources] = useState<Array<Record<string, unknown>>>([])
  const [activeToolCalls, setActiveToolCalls] = useState<ToolCallStatus[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // -------------------------------------------------------------------------
  // Refs (race condition protection)
  // -------------------------------------------------------------------------

  const abortControllerRef = useRef<AbortController | null>(null)
  const navigationRef = useRef(0)
  const streamFinalizedRef = useRef(false)

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------

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

  // -------------------------------------------------------------------------
  // Conversation Resolution
  // -------------------------------------------------------------------------

  const resolveConversation = useCallback(async () => {
    const version = ++navigationRef.current

    // Abort any active stream
    abortStream()

    setIsLoading(true)
    setError(null)
    setMessages([])
    setStreamingContent('')
    setRagSources([])
    setActiveToolCalls([])
    setIsStreaming(false)

    try {
      // Load conversations and find one for this symbol
      const result = await chatApi.listConversations(50)
      if (navigationRef.current !== version) {
        console.warn(LOG_PREFIX, 'Stale conversation resolution discarded for', symbol)
        return
      }

      if (result.total > 50) {
        console.warn(LOG_PREFIX, `Only searched first 50 of ${result.total} conversations for symbol match`)
      }

      const existing = result.conversations.find(
        (c) => c.symbol === symbol && !c.isArchived
      )

      let convId: string
      if (existing) {
        convId = existing.id
        // Load existing messages
        const msgs = await chatApi.getMessages(existing.id)
        if (navigationRef.current !== version) {
          console.warn(LOG_PREFIX, 'Stale message load discarded for', symbol)
          return
        }
        setMessages(msgs)
      } else {
        // Create new conversation
        const newConv = await chatApi.createConversation(`${symbol} Chat`, symbol)
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

  // Initialize conversation on mount
  useEffect(() => {
    resolveConversation()
  }, [resolveConversation])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
    }
  }, [])

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  const sendMessage = useCallback(
    (content: string) => {
      if (!conversationId) {
        setError('No active conversation. Please wait for loading to complete.')
        return
      }
      if (isStreaming) return

      // Reset finalization guard
      streamFinalizedRef.current = false

      // Optimistically add user message
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
                    ? { ...tc, status: event.success ? ('completed' as const) : ('failed' as const) }
                    : tc
                )
              )
            }
            break
          case 'message_end': {
            if (streamFinalizedRef.current) break // already finalized
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
        // Suppress errors from intentional abort
        if (abortControllerRef.current?.signal.aborted) return
        console.error(LOG_PREFIX, 'Stream error:', err)
        setError(getErrorMessage(err))
        setIsStreaming(false)
        setActiveToolCalls([])
      }

      const onDone = () => {
        // Finalize if streaming wasn't ended by message_end
        if (streamFinalizedRef.current) return // already handled
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
        onDone
      )
    },
    [conversationId, isStreaming, symbol, locale]
  )

  const cancelStream = useCallback(() => {
    // Commit any partial streaming content before aborting
    if (conversationId) {
      commitPartialStream(conversationId)
    }
    abortStream()
    setIsStreaming(false)
    setActiveToolCalls([])
  }, [conversationId, commitPartialStream, abortStream])

  const clearError = useCallback(() => {
    setError(null)
  }, [])

  // -------------------------------------------------------------------------
  // Context Values
  // -------------------------------------------------------------------------

  const stateValue = useMemo<StockChatState>(
    () => ({
      conversationId,
      messages,
      isStreaming,
      streamingContent,
      ragSources,
      activeToolCalls,
      isLoading,
      error,
    }),
    [conversationId, messages, isStreaming, streamingContent, ragSources, activeToolCalls, isLoading, error]
  )

  const actionsValue = useMemo<StockChatActions>(
    () => ({
      sendMessage,
      cancelStream,
      clearError,
      refreshConversation: resolveConversation,
    }),
    [sendMessage, cancelStream, clearError, resolveConversation]
  )

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <StockChatActionsContext.Provider value={actionsValue}>
      <StockChatStateContext.Provider value={stateValue}>
        {children}
      </StockChatStateContext.Provider>
    </StockChatActionsContext.Provider>
  )
}
