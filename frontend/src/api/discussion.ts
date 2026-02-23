import apiClient from './client'
import { getValidAccessToken } from '@/lib/auth'
import type {
  DiscussionSession,
  DiscussionSessionDetail,
  DiscussionStreamEvent,
} from '@/types'

export const discussionApi = {
  /**
   * Start a new discussion for a stock symbol.
   */
  startDiscussion: async (symbol: string, language?: string): Promise<DiscussionSession> => {
    const response = await apiClient.post<DiscussionSession>(
      `/discussion/${symbol}/start`,
      { language },
    )
    return response.data
  },

  /**
   * Stream discussion events via SSE (fetch + ReadableStream for auth support).
   * Returns an AbortController for cancellation.
   */
  streamDiscussion: (
    sessionId: string,
    onEvent: (data: DiscussionStreamEvent, eventId: string | null) => void,
    onError: (err: unknown) => void,
    onDone: () => void,
    options?: { lastEventId?: string },
  ): AbortController => {
    const controller = new AbortController()

    const run = async () => {
      try {
        const { createSSEParser } = await import('./sse')

        const token = await getValidAccessToken()
        const params = new URLSearchParams()
        if (options?.lastEventId && options.lastEventId !== '0-0') {
          params.set('lastEventId', options.lastEventId)
        }
        const qs = params.toString()

        const resp = await fetch(
          `/api/v1/discussion/${sessionId}/stream${qs ? `?${qs}` : ''}`,
          {
            method: 'GET',
            headers: {
              Accept: 'text/event-stream',
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            credentials: 'include',
            signal: controller.signal,
          },
        )

        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`)
        }

        const reader = resp.body?.getReader()
        if (!reader) throw new Error('No response body')

        const parser = createSSEParser<DiscussionStreamEvent>()

        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          for (const { eventId, data } of parser.feed(value)) {
            onEvent(data, eventId)
          }
        }

        onDone()
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          onError(err)
        }
      }
    }

    run()
    return controller
  },

  /**
   * Get a discussion session with all messages.
   */
  getSession: async (sessionId: string): Promise<DiscussionSessionDetail> => {
    const response = await apiClient.get<DiscussionSessionDetail>(
      `/discussion/${sessionId}`,
    )
    return response.data
  },

  /**
   * List discussion sessions for a stock (optional symbol filter).
   */
  listSessions: async (symbol?: string): Promise<DiscussionSession[]> => {
    const params = symbol ? { symbol } : {}
    const response = await apiClient.get<DiscussionSession[]>(
      '/discussion/sessions',
      { params },
    )
    return response.data
  },

  /**
   * Create a post-discussion chat conversation.
   */
  createDiscussionChat: async (sessionId: string): Promise<{ conversationId: string }> => {
    const response = await apiClient.post<{ conversationId: string }>(
      `/discussion/${sessionId}/chat`,
    )
    return response.data
  },

  deleteSession: async (sessionId: string): Promise<void> => {
    await apiClient.delete(`/discussion/${sessionId}`)
  },
}
