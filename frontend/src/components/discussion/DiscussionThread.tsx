import { useCallback, useEffect, useMemo, useRef } from 'react'
import { DiscussionMessage } from './DiscussionMessage'
import type { DiscussionMessage as DiscussionMessageType } from '@/types'

/**
 * Strip trailing JSON code blocks from streaming content.
 * Moderator outputs a JSON control block that should be hidden.
 */
function stripJsonBlocks(text: string): string {
  let cleaned = text.replace(/\s*```(?:json)?\s*\{[\s\S]*?\}\s*```\s*$/, '')
  if (cleaned !== text) return cleaned.trim()
  cleaned = text.replace(/\s*\{[^{}]*"(?:action|key_metrics|signal)"[^{}]*\}\s*$/, '')
  return cleaned.trim()
}

/** Walk up the DOM to find the nearest scrollable ancestor. */
function findScrollContainer(el: HTMLElement | null): HTMLElement {
  let node = el?.parentElement ?? null
  while (node) {
    const style = getComputedStyle(node)
    if (/(auto|scroll)/.test(style.overflow + style.overflowY)) return node
    node = node.parentElement
  }
  return document.documentElement
}

interface StreamingAgentEntry {
  content: string
  round: number
}

interface DiscussionThreadProps {
  messages: DiscussionMessageType[]
  streamingAgents: Record<string, StreamingAgentEntry>
}

/**
 * Flat chat-group style thread — messages appear in chronological order
 * as a continuous conversation, no round dividers.
 *
 * Auto-scroll only when the user is near the bottom of the scroll container.
 * If the user scrolls up to read earlier messages, auto-scroll pauses until
 * they return to the bottom.
 */
export function DiscussionThread({
  messages,
  streamingAgents,
}: DiscussionThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)

  // Track scroll position — pause auto-scroll when user scrolls up
  useEffect(() => {
    const sentinel = bottomRef.current
    if (!sentinel) return
    const container = findScrollContainer(sentinel)

    const onScroll = () => {
      const { scrollHeight, scrollTop, clientHeight } = container
      isNearBottomRef.current = scrollHeight - scrollTop - clientHeight < 150
    }

    container.addEventListener('scroll', onScroll, { passive: true })
    return () => container.removeEventListener('scroll', onScroll)
  }, [])

  // Auto-scroll only when near bottom
  const scrollToBottom = useCallback(() => {
    if (isNearBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [])

  // Scroll on new finalized messages
  useEffect(scrollToBottom, [messages.length, scrollToBottom])

  // Scroll on streaming content updates (debounced via rAF in parent)
  useEffect(scrollToBottom, [streamingAgents, scrollToBottom])

  // Streaming agents — use natural order (only 1 at a time with sequential execution)
  const streamingEntries = useMemo(() => {
    return Object.entries(streamingAgents)
      .filter(([, data]) => data.content)
  }, [streamingAgents])

  return (
    <div className="space-y-0">
      {messages.map((msg) => (
        <DiscussionMessage
          key={msg.id}
          agentType={msg.agentType}
          content={msg.content}
          toolCalls={msg.toolCalls}
        />
      ))}

      {/* Streaming agents — strip JSON control blocks from live content */}
      {streamingEntries.map(([agent, data]) => (
        <DiscussionMessage
          key={`streaming-${agent}`}
          agentType={agent}
          content={stripJsonBlocks(data.content)}
          isStreaming
        />
      ))}

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  )
}
