/**
 * Shared SSE parser that handles both `id:` and `data:` fields.
 *
 * The backend emits SSE events with an `id:` line (Redis Stream ID) for
 * reconnection support. When a connection drops, the frontend can pass
 * the last received `eventId` as a `lastEventId` query param to resume.
 */

export interface SSEParseResult<T> {
  eventId: string | null
  data: T
}

/**
 * Create a stateful SSE parser that accumulates raw bytes into typed events.
 *
 * Usage:
 * ```ts
 * const parser = createSSEParser<MyEvent>()
 * const { done, value } = await reader.read()
 * const events = parser.feed(value)
 * for (const { eventId, data } of events) { ... }
 * ```
 */
export function createSSEParser<T>() {
  let buffer = ''
  let currentEventId: string | null = null
  const decoder = new TextDecoder()

  return {
    /** Feed raw bytes from ReadableStream, returns parsed events. */
    feed(chunk: Uint8Array): SSEParseResult<T>[] {
      buffer += decoder.decode(chunk, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      const events: SSEParseResult<T>[] = []

      for (const line of lines) {
        const trimmed = line.trim()

        if (trimmed.startsWith('id: ')) {
          // SSE id field — track for reconnection
          currentEventId = trimmed.slice(4).trim()
        } else if (trimmed.startsWith('data: ')) {
          // SSE data field — parse JSON payload
          try {
            const parsed = JSON.parse(trimmed.slice(6)) as T
            events.push({ eventId: currentEventId, data: parsed })
            currentEventId = null
          } catch {
            // Skip unparseable data lines (e.g. heartbeat comments)
          }
        }
        // Lines starting with ':' are SSE comments (heartbeat) — ignore
      }

      return events
    },
  }
}
