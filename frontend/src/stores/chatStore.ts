import { create } from 'zustand'
import type { ChatConversation, ChatMessage, ChatStreamEvent, ToolCallStatus } from '@/types'
import { chatApi } from '@/api'
import { getErrorMessage } from '@/api/client'

interface ChatState {
  conversations: ChatConversation[]
  currentConversationId: string | null
  messages: ChatMessage[]
  isLoadingConversations: boolean
  isLoadingMessages: boolean
  isStreaming: boolean
  streamingContent: string
  ragSources: Array<Record<string, unknown>>
  activeToolCalls: ToolCallStatus[]
  error: string | null
  total: number
}

interface ChatActions {
  loadConversations: (limit?: number, offset?: number) => Promise<void>
  createConversation: (title?: string, symbol?: string) => Promise<void>
  selectConversation: (id: string) => Promise<void>
  deleteConversation: (id: string) => Promise<void>
  updateConversation: (id: string, updates: { title?: string; isArchived?: boolean }) => Promise<void>
  loadMessages: (conversationId: string, limit?: number, offset?: number) => Promise<void>
  sendMessage: (content: string, symbol?: string, language?: string) => Promise<void>
  cancelStream: () => void
  clearError: () => void
}

type ChatStore = ChatState & ChatActions

let _abortController: AbortController | null = null

export const useChatStore = create<ChatStore>((set, get) => ({
  // State
  conversations: [],
  currentConversationId: null,
  messages: [],
  isLoadingConversations: false,
  isLoadingMessages: false,
  isStreaming: false,
  streamingContent: '',
  ragSources: [],
  activeToolCalls: [],
  error: null,
  total: 0,

  // Actions
  loadConversations: async (limit = 20, offset = 0): Promise<void> => {
    set({ isLoadingConversations: true, error: null })

    try {
      const result = await chatApi.listConversations(limit, offset)
      set({
        conversations: result.conversations,
        total: result.total,
        isLoadingConversations: false,
      })
    } catch (error) {
      const message = getErrorMessage(error)
      set({ error: message, isLoadingConversations: false })
    }
  },

  createConversation: async (title?: string, symbol?: string): Promise<void> => {
    set({ error: null })

    try {
      const conversation = await chatApi.createConversation(title, symbol)
      set((state) => ({
        conversations: [conversation, ...state.conversations],
        currentConversationId: conversation.id,
        messages: [],
        total: state.total + 1,
      }))
    } catch (error) {
      const message = getErrorMessage(error)
      set({ error: message })
    }
  },

  selectConversation: async (id: string): Promise<void> => {
    set({ currentConversationId: id, messages: [], streamingContent: '', ragSources: [], activeToolCalls: [] })
    await get().loadMessages(id)
  },

  deleteConversation: async (id: string): Promise<void> => {
    set({ error: null })

    try {
      await chatApi.deleteConversation(id)
      const { currentConversationId } = get()
      set((state) => ({
        conversations: state.conversations.filter((c) => c.id !== id),
        total: state.total - 1,
        ...(currentConversationId === id
          ? { currentConversationId: null, messages: [], streamingContent: '', ragSources: [], activeToolCalls: [] }
          : {}),
      }))
    } catch (error) {
      const message = getErrorMessage(error)
      set({ error: message })
    }
  },

  updateConversation: async (id: string, updates: { title?: string; isArchived?: boolean }): Promise<void> => {
    set({ error: null })

    try {
      const updated = await chatApi.updateConversation(id, updates)
      set((state) => ({
        conversations: state.conversations.map((c) => (c.id === id ? updated : c)),
      }))
    } catch (error) {
      const message = getErrorMessage(error)
      set({ error: message })
    }
  },

  loadMessages: async (conversationId: string, limit = 50, offset = 0): Promise<void> => {
    set({ isLoadingMessages: true, error: null })

    try {
      const messages = await chatApi.getMessages(conversationId, limit, offset)
      set({ messages, isLoadingMessages: false })
    } catch (error) {
      const message = getErrorMessage(error)
      set({ error: message, isLoadingMessages: false })
    }
  },

  sendMessage: async (content: string, symbol?: string, language?: string): Promise<void> => {
    const { currentConversationId } = get()
    if (!currentConversationId) return
    // Default to browser language if not specified
    const lang = language ?? navigator.language ?? 'en'

    set({ error: null })

    // Optimistically add user message
    const userMessage: ChatMessage = {
      id: `temp-${Date.now()}`,
      conversationId: currentConversationId,
      role: 'user',
      content,
      tokenCount: null,
      model: null,
      toolCalls: null,
      ragContext: null,
      createdAt: new Date().toISOString(),
    }

    set((state) => ({
      messages: [...state.messages, userMessage],
      isStreaming: true,
      streamingContent: '',
      ragSources: [],
      activeToolCalls: [],
    }))

    const onEvent = (event: ChatStreamEvent) => {
      switch (event.type) {
        case 'content_delta':
          if (event.content) {
            set((state) => ({
              streamingContent: state.streamingContent + event.content,
            }))
          }
          break
        case 'rag_sources':
          if (event.sources) {
            set({ ragSources: event.sources })
          }
          break
        case 'tool_call_start':
          if (event.toolCallId && event.toolName) {
            set((state) => ({
              activeToolCalls: [
                ...state.activeToolCalls,
                {
                  id: event.toolCallId!,
                  name: event.toolName!,
                  label: event.toolLabel ?? event.toolName!,
                  status: 'running' as const,
                },
              ],
            }))
          }
          break
        case 'tool_call_result':
          if (event.toolCallId) {
            set((state) => ({
              activeToolCalls: state.activeToolCalls.map((tc) =>
                tc.id === event.toolCallId
                  ? { ...tc, status: event.success ? 'completed' as const : 'failed' as const }
                  : tc
              ),
            }))
          }
          break
        case 'message_end': {
          const assistantMessage: ChatMessage = {
            id: event.messageId ?? `msg-${Date.now()}`,
            conversationId: currentConversationId,
            role: 'assistant',
            content: get().streamingContent,
            tokenCount: event.tokenCount ?? null,
            model: event.model ?? null,
            toolCalls: null,
            ragContext: null,
            createdAt: new Date().toISOString(),
          }
          set((state) => ({
            messages: [...state.messages, assistantMessage],
            streamingContent: '',
            isStreaming: false,
            activeToolCalls: [],
          }))
          break
        }
        case 'error':
          set({
            error: event.error ?? 'An error occurred during streaming',
            isStreaming: false,
            activeToolCalls: [],
          })
          break
      }
    }

    const onError = (err: unknown) => {
      const message = getErrorMessage(err)
      set({ error: message, isStreaming: false, activeToolCalls: [] })
    }

    const onDone = () => {
      // If streaming hasn't been marked complete by message_end, finalize it
      if (get().isStreaming) {
        const remaining = get().streamingContent
        if (remaining) {
          const assistantMessage: ChatMessage = {
            id: `msg-${Date.now()}`,
            conversationId: currentConversationId,
            role: 'assistant',
            content: remaining,
            tokenCount: null,
            model: null,
            toolCalls: null,
            ragContext: null,
            createdAt: new Date().toISOString(),
          }
          set((state) => ({
            messages: [...state.messages, assistantMessage],
            streamingContent: '',
            isStreaming: false,
            activeToolCalls: [],
          }))
        } else {
          set({ isStreaming: false, activeToolCalls: [] })
        }
      }
    }

    _abortController = chatApi.streamMessage(
      currentConversationId,
      content,
      symbol,
      lang,
      onEvent,
      onError,
      onDone,
    )
  },

  cancelStream: () => {
    _abortController?.abort()
    _abortController = null
    set({ isStreaming: false, streamingContent: '', activeToolCalls: [] })
  },

  clearError: () => {
    set({ error: null })
  },
}))
