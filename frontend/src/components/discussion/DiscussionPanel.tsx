import { useReducer, useEffect, useRef, useCallback, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Users,
  Play,
  Loader2,
  AlertCircle,
  RefreshCw,
  CheckCircle2,
  Clock,
  ChevronRight,
  ChevronDown,
  ArrowLeft,
  Trash2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn, formatRelativeTime } from '@/lib/utils'
import { useLocale } from '@/hooks/useLocale'
import { useIsMobile } from '@/hooks/useIsMobile'
import { discussionApi } from '@/api/discussion'
import { DiscussionThread } from './DiscussionThread'
import { DiscussionSynthesis } from './DiscussionSynthesis'
import type {
  DiscussionMessage,
  DiscussionSession,
  DiscussionStreamEvent,
} from '@/types'

/**
 * Strip trailing JSON code blocks from message content.
 * Agents may output optional JSON metrics and the moderator outputs a required
 * JSON control block — both should be hidden from the chat UI.
 */
function stripJsonBlocks(text: string): string {
  // Strip ```json {...} ``` code blocks at the end
  let cleaned = text.replace(/\s*```(?:json)?\s*\{[\s\S]*?\}\s*```\s*$/, '')
  if (cleaned !== text) return cleaned.trim()
  // Strip bare trailing JSON with known keys
  cleaned = text.replace(/\s*\{[^{}]*"(?:action|key_metrics|signal)"[^{}]*\}\s*$/, '')
  return cleaned.trim()
}

// ---------------------------------------------------------------------------
// State management
// ---------------------------------------------------------------------------

type DiscussionPanelStatus =
  | 'idle'
  | 'loading'
  | 'discussing'
  | 'synthesizing'
  | 'completed'
  | 'failed'

interface StreamingAgentEntry {
  content: string
  round: number
}

interface DiscussionPanelState {
  status: DiscussionPanelStatus
  sessionId: string | null
  messages: DiscussionMessage[]
  currentRound: number
  streamingAgents: Record<string, StreamingAgentEntry>
  synthesisReport: string
  error: string | null
}

const INITIAL_STATE: DiscussionPanelState = {
  status: 'idle',
  sessionId: null,
  messages: [],
  currentRound: 0,
  streamingAgents: {},
  synthesisReport: '',
  error: null,
}

type DiscussionAction =
  | { type: 'START_LOADING' }
  | { type: 'SESSION_CREATED'; sessionId: string }
  | { type: 'SET_STATUS'; status: DiscussionPanelStatus }
  | { type: 'SET_STREAMING'; agent: string; round: number }
  | { type: 'APPEND_STREAMING_CONTENT'; agent: string; content: string }
  | { type: 'BATCH_APPEND_STREAMING'; chunks: Record<string, string>; synthesisChunk?: string }
  | { type: 'FINALIZE_MESSAGE'; message: DiscussionMessage }
  | { type: 'INCREMENT_ROUND' }
  | { type: 'SET_SYNTHESIS_REPORT'; report: string }
  | { type: 'SET_COMPLETED'; report: string }
  | { type: 'SET_ERROR'; error: string }
  | { type: 'RESET' }
  | { type: 'RESTORE_SESSION'; session: { sessionId: string; messages: DiscussionMessage[]; synthesisReport: string; status: DiscussionPanelStatus } }

function discussionReducer(
  state: DiscussionPanelState,
  action: DiscussionAction
): DiscussionPanelState {
  // Always log critical state transitions; skip noisy chunk appends.
  // Use console.log (NOT console.debug — Chrome hides debug by default).
  if (action.type !== 'BATCH_APPEND_STREAMING' && action.type !== 'APPEND_STREAMING_CONTENT') {
    const extra = action.type === 'FINALIZE_MESSAGE'
      ? `agent=${(action as { message: DiscussionMessage }).message?.agentType} content=${(action as { message: DiscussionMessage }).message?.content?.length ?? 0}ch`
      : ''
    console.log('[Discussion reducer]', action.type, extra,
      `| msgs=${state.messages.length} streaming=[${Object.keys(state.streamingAgents)}]`)
  }
  switch (action.type) {
    case 'START_LOADING':
      return {
        ...INITIAL_STATE,
        status: 'loading',
      }

    case 'SESSION_CREATED':
      return {
        ...state,
        sessionId: action.sessionId,
        status: 'loading',
      }

    case 'SET_STATUS':
      return {
        ...state,
        status: action.status,
      }

    case 'SET_STREAMING': {
      // If the agent already has un-finalized streaming content, salvage it
      // into messages before resetting. This prevents content loss from:
      //  - duplicate on_chain_start events in LangGraph astream_events
      //  - missed FINALIZE_MESSAGE events (e.g. empty content after stripping)
      const prev = state.streamingAgents[action.agent]
      if (prev?.content) {
        return {
          ...state,
          messages: [...state.messages, {
            id: nextMessageId(),
            round: prev.round,
            agentType: action.agent,
            content: stripJsonBlocks(prev.content),
            structuredData: null,
            toolCalls: null,
            createdAt: new Date().toISOString(),
          }],
          streamingAgents: {
            ...state.streamingAgents,
            [action.agent]: { content: '', round: action.round },
          },
        }
      }
      return {
        ...state,
        streamingAgents: {
          ...state.streamingAgents,
          [action.agent]: { content: '', round: action.round },
        },
      }
    }

    case 'APPEND_STREAMING_CONTENT': {
      const cur = state.streamingAgents[action.agent]
      if (cur) {
        return {
          ...state,
          streamingAgents: {
            ...state.streamingAgents,
            [action.agent]: { ...cur, content: cur.content + action.content },
          },
        }
      }
      return {
        ...state,
        streamingAgents: {
          ...state.streamingAgents,
          [action.agent]: { content: action.content, round: state.currentRound },
        },
      }
    }

    case 'BATCH_APPEND_STREAMING': {
      const updated = { ...state.streamingAgents }
      for (const [agent, text] of Object.entries(action.chunks)) {
        const cur = updated[agent]
        if (cur) {
          updated[agent] = { ...cur, content: cur.content + text }
        } else {
          updated[agent] = { content: text, round: state.currentRound }
        }
      }
      return {
        ...state,
        streamingAgents: updated,
        synthesisReport: action.synthesisChunk
          ? state.synthesisReport + action.synthesisChunk
          : state.synthesisReport,
        status: action.synthesisChunk ? 'synthesizing' : state.status,
      }
    }

    case 'FINALIZE_MESSAGE': {
      const { [action.message.agentType]: _, ...remaining } = state.streamingAgents
      return {
        ...state,
        messages: [...state.messages, action.message],
        streamingAgents: remaining,
      }
    }

    // INCREMENT_ROUND: only bump the counter — do NOT clear streamingAgents.
    // FINALIZE_MESSAGE handles removal individually. Bulk-clearing was causing
    // content to vanish when events arrived in unexpected order.
    case 'INCREMENT_ROUND':
      return {
        ...state,
        currentRound: state.currentRound + 1,
      }

    case 'SET_SYNTHESIS_REPORT':
      return {
        ...state,
        synthesisReport: action.report,
      }

    case 'SET_COMPLETED': {
      // Safety net: flush any remaining streaming content to messages
      // before clearing streamingAgents. This catches cases where
      // FINALIZE_MESSAGE didn't fire (e.g. due to event timing issues).
      const flushedMessages = [...state.messages]
      const remainingAgents = Object.entries(state.streamingAgents)
      if (remainingAgents.length > 0) {
        console.log('[Discussion] SET_COMPLETED: flushing', remainingAgents.length,
          'remaining streaming agents:', remainingAgents.map(([a, d]) => `${a}(${d.content.length}ch)`))
        for (const [agent, data] of remainingAgents) {
          if (data.content) {
            flushedMessages.push({
              id: nextMessageId(),
              round: data.round,
              agentType: agent,
              content: stripJsonBlocks(data.content),
              structuredData: null,
              toolCalls: null,
              createdAt: new Date().toISOString(),
            })
          }
        }
      }
      return {
        ...state,
        status: 'completed',
        messages: flushedMessages,
        // Use the longer text: complete event may have a fuller report than
        // accumulated streaming chunks (or vice-versa if stream was cut).
        synthesisReport: (action.report && action.report.length >= state.synthesisReport.length)
          ? action.report
          : state.synthesisReport,
        streamingAgents: {},
        error: null,
      }
    }

    case 'SET_ERROR':
      return {
        ...state,
        status: 'failed',
        error: action.error,
        streamingAgents: {},
      }

    case 'RESET':
      return INITIAL_STATE

    case 'RESTORE_SESSION':
      return {
        ...INITIAL_STATE,
        status: action.session.status,
        sessionId: action.session.sessionId,
        messages: action.session.messages,
        synthesisReport: action.session.synthesisReport,
      }

    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Cache key & TTL
// ---------------------------------------------------------------------------

const CACHE_TTL_MS = 30 * 60 * 1000 // 30 minutes

interface CachedDiscussionResult {
  sessionId: string
  messages: DiscussionMessage[]
  synthesisReport: string
  completedAt: number
}

// ---------------------------------------------------------------------------
// Unique message ID counter for streaming messages
// ---------------------------------------------------------------------------

/** Generate a unique message ID. crypto.randomUUID() requires HTTPS;
 *  fall back to a simple random ID for HTTP / non-secure contexts. */
let _msgCounter = 0
function nextMessageId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `msg-${Date.now()}-${++_msgCounter}-${Math.random().toString(36).slice(2, 8)}`
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface DiscussionPanelProps {
  symbol: string
  /** When true, automatically starts the discussion on mount (used by mobile DiscussionPage). */
  autoStart?: boolean
}

// Version marker — if this shows in console, user has latest code
const DISCUSSION_VERSION = 'v3-2026-02-21'

export default function DiscussionPanel({ symbol, autoStart }: DiscussionPanelProps) {
  const { t } = useTranslation('common')
  const { locale } = useLocale()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isMobile = useIsMobile()

  const [state, dispatch] = useReducer(discussionReducer, INITIAL_STATE)
  const [threadExpanded, setThreadExpanded] = useState(true)
  const abortRef = useRef<AbortController | null>(null)
  const lastEventIdRef = useRef('0-0')
  const reconnectCountRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Refs to mirror reducer state for use inside SSE callback closure
  const stateRef = useRef(state)
  stateRef.current = state

  // Chunk buffering — accumulate per-agent chunks and flush once per animation frame
  // to avoid ~8000 individual React re-renders during token streaming
  const chunkBufferRef = useRef<Record<string, string>>({})
  const synthesisBufferRef = useRef('')
  const rafRef = useRef<number | null>(null)

  const flushChunkBuffer = useCallback(() => {
    rafRef.current = null
    const agentChunks = chunkBufferRef.current
    const synthChunk = synthesisBufferRef.current
    if (Object.keys(agentChunks).length === 0 && !synthChunk) return
    chunkBufferRef.current = {}
    synthesisBufferRef.current = ''
    dispatch({
      type: 'BATCH_APPEND_STREAMING',
      chunks: agentChunks,
      ...(synthChunk ? { synthesisChunk: synthChunk } : {}),
    })
  }, [])

  const scheduleFlush = useCallback(() => {
    if (rafRef.current === null) {
      rafRef.current = requestAnimationFrame(flushChunkBuffer)
    }
  }, [flushChunkBuffer])

  // Clean up rAF on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current)
    }
  }, [])

  // Configure React Query GC to match our cache TTL (once)
  const defaultsSetRef = useRef(false)
  if (!defaultsSetRef.current) {
    queryClient.setQueryDefaults(['discussion-result'], { gcTime: CACHE_TTL_MS })
    defaultsSetRef.current = true
  }

  // ---- Past sessions query ----
  const pastSessionsQuery = useQuery({
    queryKey: ['discussion-sessions', symbol],
    queryFn: () => discussionApi.listSessions(symbol),
    staleTime: CACHE_TTL_MS,
    enabled: state.status === 'idle',
  })

  // ---- Restore from cache or reset on symbol change ----
  useEffect(() => {
    // Abort any active stream and pending reconnect
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    // Try cache restore
    try {
      const cached = queryClient.getQueryData<CachedDiscussionResult>([
        'discussion-result',
        symbol,
      ])
      if (cached && Date.now() - cached.completedAt < CACHE_TTL_MS) {
        dispatch({
          type: 'RESTORE_SESSION',
          session: {
            sessionId: cached.sessionId,
            messages: cached.messages,
            synthesisReport: cached.synthesisReport,
            status: 'completed',
          },
        })
        return
      }
    } catch {
      queryClient.removeQueries({ queryKey: ['discussion-result', symbol] })
    }

    dispatch({ type: 'RESET' })
    lastEventIdRef.current = '0-0'
    reconnectCountRef.current = 0
    reconnectCheckedRef.current = false
  }, [symbol, queryClient])

  // ---- Cleanup on unmount ----
  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort()
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
    }
  }, [])

  // ---- SSE event handler ----
  const handleStreamEvent = useCallback(
    (event: DiscussionStreamEvent) => {
      // Always log non-chunk SSE events (chunk events are too noisy).
      // Use console.log (NOT console.debug — Chrome hides debug by default).
      if (!event.type.includes('chunk') && event.type !== 'heartbeat') {
        console.log('[Discussion SSE]', event.type,
          event.agent_type ?? '',
          event.content ? `content=${event.content.length}ch` : '(no content)',
          `| msgs=${stateRef.current.messages.length} streaming=[${Object.keys(stateRef.current.streamingAgents)}]`)
      }
      switch (event.type) {
        case 'heartbeat':
          break

        case 'discussion_start':
          console.log(`[Discussion] === Stream started (${DISCUSSION_VERSION}) ===`)
          dispatch({ type: 'SET_STATUS', status: 'loading' })
          break

        case 'data_fetch_start':
          dispatch({ type: 'SET_STATUS', status: 'loading' })
          break

        case 'data_fetch_complete':
          dispatch({ type: 'SET_STATUS', status: 'discussing' })
          break

        case 'agent_statement_start':
        case 'agent_response_start':
          if (event.agent_type) {
            dispatch({
              type: 'SET_STREAMING',
              agent: event.agent_type,
              round: event.round ?? stateRef.current.currentRound,
            })
          }
          break

        case 'agent_statement_chunk':
        case 'agent_response_chunk':
          if (event.content && event.agent_type) {
            // Buffer chunks and flush once per animation frame for performance
            const buf = chunkBufferRef.current
            buf[event.agent_type] = (buf[event.agent_type] ?? '') + event.content
            scheduleFlush()
          }
          break

        case 'agent_statement_complete':
        case 'agent_response_complete':
          flushChunkBuffer()
          if (event.agent_type) {
            // Prefer the authoritative content from the complete event.
            // Fall back to accumulated streaming content if the event content
            // is empty (e.g. strip_json_blocks removed everything).
            const streamContent = stateRef.current.streamingAgents[event.agent_type]?.content ?? ''
            const finalContent = event.content
              ? stripJsonBlocks(event.content)
              : stripJsonBlocks(streamContent)

            // Always finalize if we have any content. The reducer handles
            // the case where the agent isn't in streamingAgents gracefully.
            // Previous guard `if (!isInStreaming && !event.content) break`
            // was too aggressive — stateRef can be stale from React batching.
            if (finalContent || streamContent) {
              dispatch({
                type: 'FINALIZE_MESSAGE',
                message: {
                  id: nextMessageId(),
                  round: event.round ?? stateRef.current.currentRound,
                  agentType: event.agent_type,
                  content: finalContent,
                  structuredData: null,
                  toolCalls: null,
                  createdAt: new Date().toISOString(),
                },
              })
            }
          }
          break

        case 'agent_tool_call':
          // Tool calls are informational; could be surfaced later
          break

        case 'debate_round_start':
          dispatch({ type: 'INCREMENT_ROUND' })
          break

        case 'moderator_review_start':
          flushChunkBuffer()
          dispatch({
            type: 'SET_STREAMING',
            agent: 'moderator',
            round: stateRef.current.currentRound,
          })
          break

        case 'moderator_chunk':
          if (event.content) {
            const buf = chunkBufferRef.current
            buf['moderator'] = (buf['moderator'] ?? '') + event.content
            scheduleFlush()
          }
          break

        case 'moderator_guidance': {
          flushChunkBuffer()
          const rawModeratorContent = typeof event.content === 'string' ? event.content : ''

          // Finalize moderator message as a visible chat bubble (strip JSON control block).
          // Fall back to streaming content if the event content is empty after stripping.
          const streamModContent = stateRef.current.streamingAgents['moderator']?.content ?? ''
          const finalModContent = rawModeratorContent
            ? stripJsonBlocks(rawModeratorContent)
            : stripJsonBlocks(streamModContent)

          // Always finalize if we have content — don't guard on stateRef
          // which may be stale during React 18 batching.
          if (finalModContent || streamModContent) {
            dispatch({
              type: 'FINALIZE_MESSAGE',
              message: {
                id: nextMessageId(),
                round: stateRef.current.currentRound,
                agentType: 'moderator',
                content: finalModContent,
                structuredData: null,
                toolCalls: null,
                createdAt: new Date().toISOString(),
              },
            })
          }
          break
        }

        case 'synthesis_start':
          flushChunkBuffer()
          dispatch({ type: 'SET_STATUS', status: 'synthesizing' })
          break

        case 'synthesis_chunk':
          if (event.content) {
            synthesisBufferRef.current += event.content
            scheduleFlush()
          }
          break

        case 'synthesis_complete':
          flushChunkBuffer()
          if (event.content) {
            // Use whichever is longer: the complete event's content (authoritative)
            // or the accumulated chunks (in case the complete event is truncated)
            const accumulated = stateRef.current.synthesisReport
            const report = event.content.length >= accumulated.length
              ? event.content
              : accumulated
            dispatch({ type: 'SET_SYNTHESIS_REPORT', report })
          }
          break

        case 'discussion_complete': {
          flushChunkBuffer()
          // Log final state before completion for diagnosis
          console.log(`[Discussion] === COMPLETE === msgs=${stateRef.current.messages.length}`,
            `streaming=[${Object.entries(stateRef.current.streamingAgents).map(([a, d]) => `${a}:${d.content.length}ch`)}]`,
            `msgAgents=[${stateRef.current.messages.map(m => `${m.agentType}(r${m.round})`)}]`)

          // After API flattening, synthesis_report is top-level
          const report =
            typeof event['synthesis_report'] === 'string'
              ? event['synthesis_report']
              : stateRef.current.synthesisReport

          // Compute final messages BEFORE dispatching SET_COMPLETED —
          // stateRef won't reflect reducer output until next render cycle.
          // This mirrors the SET_COMPLETED reducer's flush logic.
          const finalMessages = [...stateRef.current.messages]
          for (const [agent, data] of Object.entries(stateRef.current.streamingAgents)) {
            if (data.content) {
              finalMessages.push({
                id: nextMessageId(),
                round: data.round,
                agentType: agent,
                content: stripJsonBlocks(data.content),
                structuredData: null,
                toolCalls: null,
                createdAt: new Date().toISOString(),
              })
            }
          }

          dispatch({ type: 'SET_COMPLETED', report })

          // Write cache with pre-computed values (no stale stateRef issue)
          queryClient.setQueryData<CachedDiscussionResult>(
            ['discussion-result', symbol],
            {
              sessionId: stateRef.current.sessionId ?? '',
              messages: finalMessages,
              synthesisReport: report,
              completedAt: Date.now(),
            }
          )

          // Invalidate past sessions so the new one appears
          queryClient.invalidateQueries({
            queryKey: ['discussion-sessions', symbol],
          })
          break
        }

        case 'error':
          dispatch({
            type: 'SET_ERROR',
            error: event.error ?? t('discussion.error'),
          })
          break

        case 'timeout':
          dispatch({
            type: 'SET_ERROR',
            error: 'Discussion timeout',
          })
          break

        default:
          console.log('[Discussion SSE] unhandled:', event.type)
          break
      }
    },
    [queryClient, symbol, t, scheduleFlush, flushChunkBuffer]
  )

  // ---- View a past session ----
  const viewSession = useCallback(
    async (sessionId: string) => {
      dispatch({ type: 'START_LOADING' })
      setThreadExpanded(false)

      try {
        const detail = await discussionApi.getSession(sessionId)
        dispatch({
          type: 'RESTORE_SESSION',
          session: {
            sessionId: detail.id,
            messages: detail.messages,
            synthesisReport: detail.synthesisReport ?? '',
            status: detail.status === 'completed' ? 'completed' : 'failed',
          },
        })
      } catch (err) {
        dispatch({
          type: 'SET_ERROR',
          error: err instanceof Error ? err.message : t('discussion.error'),
        })
      }
    },
    [t],
  )

  // ---- Core: connect to an existing session's SSE stream ----
  const connectToStream = useCallback(
    (sessionId: string) => {
      if (abortRef.current) {
        abortRef.current.abort()
        abortRef.current = null
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }

      const controller = discussionApi.streamDiscussion(
        sessionId,
        (event, eventId) => {
          if (eventId) lastEventIdRef.current = eventId
          handleStreamEvent(event)
        },
        (err) => {
          // 409 = session already completed/failed — load from DB instead
          if (err instanceof Error && err.message.includes('409')) {
            viewSession(sessionId)
            return
          }

          // Auto-reconnect if we had progress and haven't exceeded retry limit
          if (lastEventIdRef.current !== '0-0' && reconnectCountRef.current < 3) {
            reconnectCountRef.current++
            console.log(`[Discussion] Reconnecting (${reconnectCountRef.current}/3)...`)
            dispatch({ type: 'SET_STATUS', status: 'loading' })
            reconnectTimerRef.current = setTimeout(() => {
              connectToStream(sessionId)
            }, 2000)
            return
          }

          dispatch({
            type: 'SET_ERROR',
            error: err instanceof Error ? err.message : t('discussion.error'),
          })
        },
        () => {
          // Stream ended — check if it was a clean completion or premature close
          const currentStatus = stateRef.current.status
          if (currentStatus === 'completed' || currentStatus === 'failed') return

          // Premature close — try auto-reconnect if we had progress
          if (lastEventIdRef.current !== '0-0' && reconnectCountRef.current < 3) {
            reconnectCountRef.current++
            console.log(`[Discussion] Stream ended prematurely. Reconnecting (${reconnectCountRef.current}/3)...`)
            dispatch({ type: 'SET_STATUS', status: 'loading' })
            reconnectTimerRef.current = setTimeout(() => {
              connectToStream(sessionId)
            }, 2000)
            return
          }

          // Give up — show result if we have synthesis, otherwise error
          if (stateRef.current.synthesisReport) {
            dispatch({
              type: 'SET_COMPLETED',
              report: stateRef.current.synthesisReport,
            })
          } else {
            dispatch({
              type: 'SET_ERROR',
              error: t('discussion.error'),
            })
          }
        },
        lastEventIdRef.current !== '0-0'
          ? { lastEventId: lastEventIdRef.current }
          : {},
      )

      abortRef.current = controller
    },
    [handleStreamEvent, viewSession, t],
  )

  // ---- Start new discussion ----
  const startDiscussion = useCallback(async () => {
    // Abort previous stream if any
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }

    // Clear cache and reset
    queryClient.removeQueries({ queryKey: ['discussion-result', symbol] })
    lastEventIdRef.current = '0-0'
    reconnectCountRef.current = 0

    dispatch({ type: 'START_LOADING' })
    setThreadExpanded(true)

    try {
      const lang = locale.toLowerCase().startsWith('zh') ? 'zh' : 'en'
      const session = await discussionApi.startDiscussion(symbol, lang)
      dispatch({ type: 'SESSION_CREATED', sessionId: session.id })
      connectToStream(session.id)
    } catch (err) {
      dispatch({
        type: 'SET_ERROR',
        error: err instanceof Error ? err.message : t('discussion.error'),
      })
    }
  }, [symbol, locale, queryClient, connectToStream, t])

  // ---- Reconnect to an in-progress discussion (from mount) ----
  const reconnectToDiscussion = useCallback(
    async (sessionId: string) => {
      dispatch({
        type: 'RESTORE_SESSION',
        session: {
          sessionId,
          messages: [],
          synthesisReport: '',
          status: 'loading',
        },
      })
      setThreadExpanded(true)

      // Check the real session status before deciding how to reconnect.
      // If the session already completed (e.g. background task finished while
      // we were on another page), load from DB to avoid replaying 2000+ events
      // at once — which would look like "instant completion" to the user.
      try {
        const detail = await discussionApi.getSession(sessionId)
        if (detail.status === 'completed' || detail.status === 'failed') {
          console.log(`[Discussion] Session ${sessionId.slice(0, 8)} already ${detail.status}, loading from DB`)
          dispatch({
            type: 'RESTORE_SESSION',
            session: {
              sessionId: detail.id,
              messages: detail.messages,
              synthesisReport: detail.synthesisReport ?? '',
              status: detail.status === 'completed' ? 'completed' : 'failed',
            },
          })
          return
        }
      } catch {
        // Session check failed — fall through to stream reconnect
      }

      // Session still in progress — reconnect to the live stream
      lastEventIdRef.current = '0-0' // replay from start
      reconnectCountRef.current = 0
      connectToStream(sessionId)
    },
    [connectToStream],
  )

  // ---- Auto-reconnect to in-progress discussion on mount ----
  const reconnectCheckedRef = useRef(false)
  useEffect(() => {
    if (reconnectCheckedRef.current) return
    if (state.status !== 'idle' || !pastSessionsQuery.data) return
    reconnectCheckedRef.current = true

    const discussingSession = pastSessionsQuery.data.find(
      (s) => s.status === 'discussing' || s.status === 'synthesizing',
    )
    if (discussingSession) {
      console.log('[Discussion] Found in-progress session, auto-reconnecting:', discussingSession.id)
      reconnectToDiscussion(discussingSession.id)
    }
  }, [state.status, pastSessionsQuery.data, reconnectToDiscussion])

  // ---- Continue chatting ----
  const handleContinueChat = useCallback(async () => {
    if (!state.sessionId) return

    try {
      const result = await discussionApi.createDiscussionChat(state.sessionId)
      navigate(`/chat?conversation=${result.conversationId}`)
    } catch {
      // Fallback: navigate to chat page without conversation ID
      navigate('/chat')
    }
  }, [state.sessionId, navigate])

  // Collapse thread when discussion completes
  useEffect(() => {
    if (state.status === 'completed') {
      setThreadExpanded(false)
    }
  }, [state.status])

  // ---- Delete a past session ----
  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await discussionApi.deleteSession(sessionId)
      queryClient.invalidateQueries({ queryKey: ['discussion-sessions', symbol] })
    } catch {
      // Silently ignore — session may already be deleted
    }
  }, [symbol, queryClient])

  // ---- Back to history list ----
  const backToHistory = useCallback(() => {
    queryClient.removeQueries({ queryKey: ['discussion-result', symbol] })
    dispatch({ type: 'RESET' })
  }, [queryClient, symbol])

  // ---- Cancel active stream ----
  const cancelDiscussion = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    reconnectCountRef.current = 0
    dispatch({ type: 'RESET' })
  }, [])

  // ---- Auto-start (used by mobile DiscussionPage) ----
  const autoStartedRef = useRef(false)
  useEffect(() => {
    if (autoStart && !autoStartedRef.current && state.status === 'idle') {
      autoStartedRef.current = true
      startDiscussion()
    }
  }, [autoStart, state.status, startDiscussion])

  // ---- Mobile: navigate to dedicated page instead of starting inline ----
  const handleStartOrNavigate = useCallback(() => {
    if (isMobile && !autoStart) {
      navigate(`/discussion/${encodeURIComponent(symbol)}`)
    } else {
      startDiscussion()
    }
  }, [isMobile, autoStart, navigate, symbol, startDiscussion])

  // ---- Status helpers ----
  const isStreaming =
    state.status === 'loading' ||
    state.status === 'discussing' ||
    state.status === 'synthesizing'

  const statusLabel = t(`discussion.status.${state.status}`)

  // ---- Render ----
  return (
    <div className="flex flex-col">
      {/* Compact header bar */}
      <div className="flex flex-col gap-2 pb-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <StatusIcon status={state.status} />
          <h3 className="text-base font-semibold">{t('discussion.title')}</h3>
          {state.status !== 'idle' && (
            <Badge
              variant="outline"
              className={cn(
                'text-xs',
                state.status === 'completed' && 'border-stock-up/50 text-stock-up',
                state.status === 'failed' && 'border-destructive/50 text-destructive',
                isStreaming && 'border-primary/50 text-primary'
              )}
            >
              {statusLabel}
            </Badge>
          )}
        </div>

        <div className="flex items-center gap-2">
          {(state.status === 'completed' || state.status === 'failed') && (
            <Button variant="ghost" size="sm" onClick={backToHistory}>
              <ArrowLeft className="mr-1 h-4 w-4" />
              {t('stock.analysis_back', 'History')}
            </Button>
          )}
          {isStreaming ? (
            <Button variant="outline" size="sm" className="w-full sm:w-auto" onClick={cancelDiscussion}>
              {t('actions.cancel')}
            </Button>
          ) : (
            <Button
              size="sm"
              className="w-full sm:w-auto"
              onClick={handleStartOrNavigate}
              disabled={isStreaming}
            >
              {state.status === 'completed' ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {t('actions.retry')}
                </>
              ) : (
                <>
                  <Play className="mr-2 h-4 w-4" />
                  {t('discussion.startDiscussion')}
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {/* Progress indicator */}
      {isStreaming && (
        <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" />
          {state.status === 'loading' && t('discussion.status.fetchingData')}
          {state.status === 'discussing' && t('discussion.status.discussing')}
          {state.status === 'synthesizing' && t('discussion.status.synthesizing')}
        </div>
      )}

      {/* Error display */}
      {state.status === 'failed' && state.error && (
        <div role="alert" className="mb-3 flex items-center gap-2 text-sm text-destructive">
          <AlertCircle className="h-3 w-3 flex-shrink-0" />
          <span>{state.error}</span>
        </div>
      )}

      {/* Idle state: show description + past sessions */}
      {state.status === 'idle' && (
        <IdleView
          sessions={pastSessionsQuery.data}
          isLoading={pastSessionsQuery.isLoading}
          onViewSession={viewSession}
          onDeleteSession={handleDeleteSession}
        />
      )}

      {/* Active / completed discussion */}
      {state.status !== 'idle' && (
        <div className="[overscroll-behavior-y:contain]">
          {/* Synthesis report — show at top when completed */}
          {state.status === 'completed' && state.synthesisReport && (
            <div className="mb-4">
              <DiscussionSynthesis
                report={state.synthesisReport}
                onContinueChat={handleContinueChat}
              />
            </div>
          )}

          {/* Collapse/expand toggle for completed discussions */}
          {state.status === 'completed' && state.messages.length > 0 && (
            <button
              type="button"
              onClick={() => setThreadExpanded(prev => !prev)}
              className="mb-3 flex w-full items-center justify-center gap-1.5 rounded-lg border border-border/60 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            >
              <ChevronDown className={cn('h-3.5 w-3.5 transition-transform', !threadExpanded && '-rotate-90')} />
              {threadExpanded ? t('discussion.hideThread') : t('discussion.showThread')}
            </button>
          )}

          {/* Discussion thread — always visible during streaming, toggleable when completed */}
          {(isStreaming || state.status === 'failed' || threadExpanded) && (
            <DiscussionThread
              messages={state.messages}
              streamingAgents={state.streamingAgents}
            />
          )}

          {/* Synthesis report — show inline during streaming (at bottom) */}
          {state.status === 'synthesizing' && state.synthesisReport && (
            <div className="mt-4">
              <DiscussionSynthesis
                report={state.synthesisReport}
                onContinueChat={handleContinueChat}
                isStreaming
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusIcon({ status }: { status: DiscussionPanelStatus }) {
  switch (status) {
    case 'loading':
    case 'discussing':
    case 'synthesizing':
      return <Loader2 className="h-4 w-4 animate-spin text-primary" />
    case 'completed':
      return <CheckCircle2 className="h-4 w-4 text-stock-up" />
    case 'failed':
      return <AlertCircle className="h-4 w-4 text-destructive" />
    default:
      return <Users className="h-4 w-4" />
  }
}

interface IdleViewProps {
  sessions: DiscussionSession[] | undefined
  isLoading: boolean
  onViewSession: (id: string) => void
  onDeleteSession: (id: string, e: React.MouseEvent) => void
}

function IdleView({ sessions, isLoading, onViewSession, onDeleteSession }: IdleViewProps) {
  const { t } = useTranslation('common')

  return (
    <div className="space-y-6">
      {/* Empty state description */}
      <div className="flex min-h-[120px] items-center justify-center py-4">
        <div className="text-center">
          <Users className="mx-auto mb-3 h-10 w-10 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {t('discussion.startDiscussionDesc')}
          </p>
        </div>
      </div>

      {/* Past sessions */}
      {isLoading && (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      )}

      {sessions && sessions.length > 0 && (
        <div>
          <h4 className="mb-2 text-sm font-medium text-muted-foreground">
            {t('discussion.pastSessions')}
          </h4>
          <div className="space-y-2">
            {sessions.map((session) => (
              <div
                key={session.id}
                role="button"
                tabIndex={0}
                onClick={() => onViewSession(session.id)}
                onKeyDown={(e) => { if (e.key === 'Enter') onViewSession(session.id) }}
                className={cn(
                  'group flex w-full items-center justify-between rounded-lg border p-3',
                  'text-left transition-colors hover:bg-muted/50 cursor-pointer',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2'
                )}
              >
                <div className="flex items-center gap-3">
                  <SessionStatusBadge status={session.status} />
                  <div>
                    <p className="text-sm font-medium">
                      {session.symbol}
                    </p>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      <span>{formatRelativeTime(session.createdAt)}</span>
                      <span className="text-border">|</span>
                      <span>
                        {session.discussionRounds}/{session.maxRounds}{' '}
                        {t('discussion.roundsLabel')}
                      </span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className="rounded p-1 opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                    onClick={(e) => onDeleteSession(session.id, e)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {sessions && sessions.length === 0 && !isLoading && (
        <p className="text-center text-sm text-muted-foreground">
          {t('discussion.noSessions')}
        </p>
      )}
    </div>
  )
}

function SessionStatusBadge({ status }: { status: string }) {
  const isComplete = status === 'completed'
  // Sessions with "discussing"/"synthesizing" status in the past sessions list
  // are orphaned (the stream was interrupted). Treat them as failed.
  const isFailed = status === 'failed' || status === 'discussing' || status === 'synthesizing'

  return (
    <div
      className={cn(
        'flex h-8 w-8 items-center justify-center rounded-full',
        isComplete && 'bg-stock-up/10',
        isFailed && 'bg-destructive/10',
        !isComplete && !isFailed && 'bg-muted'
      )}
    >
      {isComplete && <CheckCircle2 className="h-4 w-4 text-stock-up" />}
      {isFailed && <AlertCircle className="h-4 w-4 text-destructive" />}
      {!isComplete && !isFailed && (
        <Clock className="h-4 w-4 text-muted-foreground" />
      )}
    </div>
  )
}
