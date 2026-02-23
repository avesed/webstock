import { useReducer, useCallback, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { LucideIcon } from 'lucide-react'
import {
  Brain,
  TrendingUp,
  BarChart3,
  MessageSquare,
  Newspaper,
  Loader2,
  AlertCircle,
  RefreshCw,
  CheckCircle2,
  XCircle,
  Clock,
  ArrowLeft,
  Trash2,
} from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn, formatRelativeTime } from '@/lib/utils'
import { analysisApi } from '@/api'
import { getValidAccessToken } from '@/lib/auth'
import { useLocale } from '@/hooks/useLocale'
import { createSSEParser } from '@/api/sse'

// ---------------------------------------------------------------------------
// Props & local types
// ---------------------------------------------------------------------------

interface AnalysisPanelProps {
  symbol: string
  className?: string
}

interface AgentStatus {
  name: string
  icon: LucideIcon
  status: 'idle' | 'running' | 'complete' | 'error'
  latencyMs?: number
}

interface AgentResult {
  agent: string
  summary: string
  keyInsights: Array<{ title: string; description: string; importance: string }>
}

interface SSEEvent {
  type:
    | 'heartbeat'
    | 'start'
    | 'analysis_phase_start'
    | 'agent_start'
    | 'agent_complete'
    | 'analysis_phase_complete'
    | 'synthesis_start'
    | 'synthesis_pending'
    | 'synthesis_chunk'
    | 'clarification_needed'
    | 'clarification_start'
    | 'clarification_complete'
    | 'data_fetch_start'
    | 'data_fetch_complete'
    | 'complete'
    | 'timeout'
    | 'error'
  agent?: string
  content?: string
  message?: string
  error?: string
  success?: boolean
  latency_ms?: number
  summary?: string
  key_insights?: Array<{ title: string; description: string; importance: string }>
  synthesis_output?: string
  agents_completed?: number
  timestamp?: number
}

interface CachedAnalysisResult {
  agentResults: AgentResult[]
  synthesisContent: string
  clarificationRound: number
  agentStatuses: Record<string, { status: AgentStatus['status']; latencyMs?: number }>
  completedAt: number
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CACHE_TTL_MS = 30 * 60 * 1000 // 30 minutes
const VALID_AGENTS = new Set(['fundamental', 'technical', 'sentiment', 'news'])

const AGENT_ICONS: Record<string, LucideIcon> = {
  fundamental: BarChart3,
  technical: TrendingUp,
  sentiment: MessageSquare,
  news: Newspaper,
}

function createInitialAgentStatus(): Record<string, AgentStatus> {
  return {
    fundamental: { name: 'fundamental', icon: BarChart3, status: 'idle' },
    technical: { name: 'technical', icon: TrendingUp, status: 'idle' },
    sentiment: { name: 'sentiment', icon: MessageSquare, status: 'idle' },
    news: { name: 'news', icon: Newspaper, status: 'idle' },
  }
}

// ---------------------------------------------------------------------------
// State management (useReducer)
// ---------------------------------------------------------------------------

type StreamStatus = 'idle' | 'connecting' | 'analyzing' | 'synthesizing' | 'complete' | 'error'

interface AnalysisState {
  status: StreamStatus
  agents: Record<string, AgentStatus>
  agentResults: AgentResult[]
  synthesisContent: string
  clarificationRound: number
  error: string | null
  progress: string
}

const INITIAL_STATE: AnalysisState = {
  status: 'idle',
  agents: createInitialAgentStatus(),
  agentResults: [],
  synthesisContent: '',
  clarificationRound: 0,
  error: null,
  progress: '',
}

type AnalysisAction =
  | { type: 'START_ANALYSIS'; progress: string }
  | { type: 'START_RECONNECT'; progress: string }
  | { type: 'RESUME_RECONNECT'; progress: string }
  | { type: 'SET_STATUS'; status: StreamStatus }
  | { type: 'SET_PROGRESS'; progress: string }
  | { type: 'AGENT_START'; agent: string }
  | { type: 'AGENT_COMPLETE'; agent: string; success: boolean; latencyMs?: number | undefined; result?: AgentResult | undefined }
  | { type: 'SYNTHESIS_CHUNK'; content: string }
  | { type: 'CLARIFICATION_NEEDED' }
  | { type: 'COMPLETE'; synthesisOutput?: string | undefined }
  | { type: 'ERROR'; error: string }
  | { type: 'CANCEL' }
  | { type: 'RESET' }
  | { type: 'BACK_TO_HISTORY' }
  | { type: 'RESTORE_FROM_CACHE'; cached: CachedAnalysisResult }
  | { type: 'RESTORE_SESSION'; agentResults: AgentResult[]; synthesisContent: string; clarificationRound: number; agentStatuses: Record<string, { status: AgentStatus['status']; latencyMs?: number }> }

function analysisReducer(state: AnalysisState, action: AnalysisAction): AnalysisState {
  switch (action.type) {
    case 'START_ANALYSIS':
      return {
        ...INITIAL_STATE,
        status: 'connecting',
        progress: action.progress,
      }

    case 'START_RECONNECT':
      // Reconnect from start (mount reconnection): reset state to replay all events
      return {
        ...INITIAL_STATE,
        status: 'connecting',
        progress: action.progress,
      }

    case 'RESUME_RECONNECT':
      // Mid-stream reconnect: keep existing state, just update progress
      return {
        ...state,
        error: null,
        progress: action.progress,
      }

    case 'SET_STATUS':
      return {
        ...state,
        status: action.status,
      }

    case 'SET_PROGRESS':
      return {
        ...state,
        progress: action.progress,
      }

    case 'AGENT_START': {
      if (!VALID_AGENTS.has(action.agent)) return state
      const current = state.agents[action.agent]
      if (!current) return state
      return {
        ...state,
        agents: {
          ...state.agents,
          [action.agent]: { ...current, status: 'running' as const },
        },
      }
    }

    case 'AGENT_COMPLETE': {
      if (!VALID_AGENTS.has(action.agent)) return state
      const current = state.agents[action.agent]
      if (!current) return state
      const updatedAgent: AgentStatus = {
        ...current,
        status: action.success ? 'complete' : 'error',
      }
      if (typeof action.latencyMs === 'number') {
        updatedAgent.latencyMs = action.latencyMs
      }
      const newResults = action.result
        ? [
            ...state.agentResults.filter((r) => r.agent !== action.agent),
            action.result,
          ]
        : state.agentResults
      return {
        ...state,
        agents: {
          ...state.agents,
          [action.agent]: updatedAgent,
        },
        agentResults: newResults,
      }
    }

    case 'SYNTHESIS_CHUNK':
      return {
        ...state,
        synthesisContent: state.synthesisContent + action.content,
      }

    case 'CLARIFICATION_NEEDED':
      return {
        ...state,
        clarificationRound: state.clarificationRound + 1,
      }

    case 'COMPLETE': {
      const finalSynthesis = action.synthesisOutput ?? state.synthesisContent
      return {
        ...state,
        status: 'complete',
        progress: '',
        synthesisContent: finalSynthesis,
      }
    }

    case 'ERROR':
      return {
        ...state,
        status: 'error',
        error: action.error,
      }

    case 'CANCEL':
      return {
        ...state,
        status: 'idle',
        progress: '',
      }

    case 'RESET':
      return INITIAL_STATE

    case 'BACK_TO_HISTORY':
      return INITIAL_STATE

    case 'RESTORE_FROM_CACHE': {
      const restoredAgents = createInitialAgentStatus()
      for (const [key, cachedAgent] of Object.entries(action.cached.agentStatuses)) {
        if (restoredAgents[key]) {
          restoredAgents[key] = { ...restoredAgents[key], ...cachedAgent }
        }
      }
      return {
        status: 'complete',
        agents: restoredAgents,
        agentResults: action.cached.agentResults,
        synthesisContent: action.cached.synthesisContent,
        clarificationRound: action.cached.clarificationRound,
        error: null,
        progress: '',
      }
    }

    case 'RESTORE_SESSION': {
      const restoredAgents = createInitialAgentStatus()
      for (const [key, val] of Object.entries(action.agentStatuses)) {
        if (restoredAgents[key]) {
          const agentUpdate: AgentStatus = { ...restoredAgents[key], status: val.status }
          if (val.latencyMs != null) {
            agentUpdate.latencyMs = val.latencyMs
          }
          restoredAgents[key] = agentUpdate
        }
      }
      return {
        status: 'complete',
        agents: restoredAgents,
        agentResults: action.agentResults,
        synthesisContent: action.synthesisContent,
        clarificationRound: action.clarificationRound,
        error: null,
        progress: '',
      }
    }

    default:
      return state
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function AnalysisPanel({ symbol, className }: AnalysisPanelProps) {
  const { locale } = useLocale()
  const { t } = useTranslation('dashboard')
  const queryClient = useQueryClient()

  // Align garbage collection with our cache TTL so results survive page navigation
  const defaultsSetRef = useRef(false)
  if (!defaultsSetRef.current) {
    queryClient.setQueryDefaults(['analysis-result'], { gcTime: CACHE_TTL_MS })
    defaultsSetRef.current = true
  }

  const [state, dispatch] = useReducer(analysisReducer, INITIAL_STATE)

  // Single ref mirroring reducer state for use in SSE callback closures & cache writes
  const stateRef = useRef(state)
  stateRef.current = state

  const abortControllerRef = useRef<AbortController | null>(null)
  const lastEventIdRef = useRef('0-0')
  const reconnectCountRef = useRef(0)
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Past analysis sessions for history view
  const pastSessionsQuery = useQuery({
    queryKey: ['analysis-sessions', symbol],
    queryFn: () => analysisApi.getSessions(symbol),
    enabled: state.status === 'idle',
    staleTime: 5 * 60 * 1000,
  })

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
      if (reconnectTimerRef.current) {
        clearTimeout(reconnectTimerRef.current)
      }
    }
  }, [])

  // Restore from cache or reset state when symbol changes
  useEffect(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    // Check for cached result (try-catch guards against corrupted cache shape)
    let restored = false
    try {
      const cached = queryClient.getQueryData<CachedAnalysisResult>(['analysis-result', symbol])
      if (cached && Date.now() - cached.completedAt < CACHE_TTL_MS) {
        dispatch({ type: 'RESTORE_FROM_CACHE', cached })
        restored = true
      }
    } catch {
      queryClient.removeQueries({ queryKey: ['analysis-result', symbol] })
    }
    if (!restored) {
      dispatch({ type: 'RESET' })
      lastEventIdRef.current = '0-0'
      reconnectCountRef.current = 0

      // Check for a running background task (e.g. user navigated away and back)
      const checkRunningTask = async () => {
        try {
          const token = await getValidAccessToken()
          const resp = await fetch(`/api/v1/analysis/${symbol}/task-status`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            credentials: 'include',
          })
          if (resp.ok) {
            const { status: taskStatus } = (await resp.json()) as { status: string }
            if (taskStatus === 'running' || taskStatus === 'completed') {
              startAnalysis({ reconnect: true })
            }
          }
        } catch {
          // Ignore -- no running task
        }
      }
      checkRunningTask()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol, queryClient])

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    switch (event.type) {
      case 'heartbeat':
        break

      case 'start':
      case 'analysis_phase_start':
        dispatch({ type: 'SET_STATUS', status: 'analyzing' })
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressAnalyzing') })
        break

      case 'data_fetch_start':
        dispatch({ type: 'SET_STATUS', status: 'analyzing' })
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressFetching') })
        break

      case 'data_fetch_complete':
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressRunning') })
        break

      case 'agent_start':
        if (event.agent && VALID_AGENTS.has(event.agent)) {
          dispatch({ type: 'AGENT_START', agent: event.agent })
        }
        break

      case 'agent_complete':
        if (event.agent && VALID_AGENTS.has(event.agent)) {
          const result: AgentResult | undefined =
            event.success && event.summary
              ? {
                  agent: event.agent,
                  summary: event.summary,
                  keyInsights: event.key_insights ?? [],
                }
              : undefined
          dispatch({
            type: 'AGENT_COMPLETE',
            agent: event.agent,
            success: event.success ?? false,
            latencyMs: event.latency_ms,
            result,
          })
        }
        break

      case 'analysis_phase_complete':
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressSynthesizing') })
        break

      case 'synthesis_start':
        dispatch({ type: 'SET_STATUS', status: 'synthesizing' })
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressGeneratingSynthesis') })
        break

      case 'synthesis_pending':
        if (event.message) {
          dispatch({ type: 'SET_PROGRESS', progress: event.message })
        }
        break

      case 'synthesis_chunk':
        if (event.content) {
          dispatch({ type: 'SYNTHESIS_CHUNK', content: event.content })
        }
        break

      case 'clarification_needed':
        dispatch({ type: 'CLARIFICATION_NEEDED' })
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressClarifying') })
        break

      case 'clarification_start':
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressClarifyingRound') })
        break

      case 'clarification_complete':
        dispatch({ type: 'SET_PROGRESS', progress: t('analysis.progressClarifyingComplete') })
        break

      case 'complete': {
        dispatch({ type: 'COMPLETE', synthesisOutput: event.synthesis_output })
        // Write to React Query cache using stateRef for the latest reducer state
        // Note: stateRef won't reflect the COMPLETE dispatch above until next render,
        // so we compute final values manually here.
        const currentState = stateRef.current
        const finalSynthesis = event.synthesis_output ?? currentState.synthesisContent
        const agentStatuses: Record<string, { status: AgentStatus['status']; latencyMs?: number }> = {}
        for (const [key, agent] of Object.entries(currentState.agents)) {
          const entry: { status: AgentStatus['status']; latencyMs?: number } = { status: agent.status }
          if (agent.latencyMs !== undefined) {
            entry.latencyMs = agent.latencyMs
          }
          agentStatuses[key] = entry
        }
        queryClient.setQueryData<CachedAnalysisResult>(['analysis-result', symbol], {
          agentResults: currentState.agentResults,
          synthesisContent: finalSynthesis,
          clarificationRound: currentState.clarificationRound,
          agentStatuses,
          completedAt: Date.now(),
        })
        break
      }

      case 'timeout':
        dispatch({ type: 'ERROR', error: t('analysis.errorTimeout') })
        break

      case 'error':
        dispatch({ type: 'ERROR', error: event.error ?? event.message ?? t('analysis.errorGeneric') })
        break
    }
  }, [queryClient, symbol, t])

  const startAnalysis = useCallback(async (options?: { reconnect?: boolean; forceNew?: boolean }) => {
    const isReconnect = options?.reconnect ?? false
    const isForceNew = options?.forceNew ?? false

    // Cancel any existing stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }

    if (!isReconnect) {
      // Fresh start or re-analyze: reset everything
      queryClient.removeQueries({ queryKey: ['analysis-result', symbol] })
      lastEventIdRef.current = '0-0'
      reconnectCountRef.current = 0
      dispatch({ type: 'START_ANALYSIS', progress: t('analysis.progressInitializing') })
    } else if (lastEventIdRef.current === '0-0') {
      // Reconnect from start (mount reconnection): reset state to replay all events
      dispatch({ type: 'START_RECONNECT', progress: t('analysis.progressReconnecting') })
    } else {
      // Mid-stream reconnect: keep existing state, resume from lastEventId
      dispatch({ type: 'RESUME_RECONNECT', progress: t('analysis.progressReconnecting') })
    }

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    try {
      const token = await getValidAccessToken()
      const lang = locale.toLowerCase().startsWith('zh') ? 'zh' : 'en'
      const params = new URLSearchParams({ language: lang })
      if (lastEventIdRef.current !== '0-0') {
        params.set('lastEventId', lastEventIdRef.current)
      }
      if (isForceNew) {
        params.set('forceNew', 'true')
      }

      const response = await fetch(
        `/api/v1/analysis/${symbol}/stream/v2?${params}`,
        {
          method: 'GET',
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          credentials: 'include',
          signal: abortController.signal,
        },
      )

      if (!response.ok) {
        throw new Error(`${t('analysis.errorFailed')}: ${response.statusText}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('Failed to get response reader')
      }

      dispatch({ type: 'SET_STATUS', status: 'analyzing' })
      reconnectCountRef.current = 0 // reset on successful connection
      const parser = createSSEParser<SSEEvent>()
      let receivedTerminal = false

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        for (const { eventId, data } of parser.feed(value)) {
          if (eventId) {
            lastEventIdRef.current = eventId
          }
          handleSSEEvent(data)
          if (data.type === 'complete' || data.type === 'error' || data.type === 'timeout') {
            receivedTerminal = true
          }
        }
      }

      // Stream ended -- if no terminal event, it was a premature close
      if (!receivedTerminal) {
        if (lastEventIdRef.current !== '0-0' && reconnectCountRef.current < 3) {
          reconnectCountRef.current++
          dispatch({
            type: 'SET_PROGRESS',
            progress: t('analysis.progressConnectionLost', {
              current: reconnectCountRef.current,
              max: 3,
            }),
          })
          reconnectTimerRef.current = setTimeout(() => {
            startAnalysis({ reconnect: true })
          }, 2000)
          return
        }
        // Give up -- show result if we have synthesis, otherwise error
        if (stateRef.current.synthesisContent) {
          dispatch({ type: 'COMPLETE' })
        } else {
          dispatch({ type: 'ERROR', error: t('analysis.errorStreamEnded') })
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }

      // Auto-reconnect if we had progress and haven't exceeded retry limit
      if (lastEventIdRef.current !== '0-0' && reconnectCountRef.current < 3) {
        reconnectCountRef.current++
        dispatch({
          type: 'SET_PROGRESS',
          progress: t('analysis.progressConnectionLost', {
            current: reconnectCountRef.current,
            max: 3,
          }),
        })
        reconnectTimerRef.current = setTimeout(() => {
          startAnalysis({ reconnect: true })
        }, 2000)
        return
      }

      dispatch({
        type: 'ERROR',
        error: err instanceof Error ? err.message : t('analysis.errorFailed'),
      })
    }
  }, [symbol, locale, handleSSEEvent, queryClient, t])

  const cancelAnalysis = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current)
      reconnectTimerRef.current = null
    }
    reconnectCountRef.current = 0
    dispatch({ type: 'CANCEL' })
  }, [])

  const restoreSession = useCallback(async (sessionId: string) => {
    try {
      dispatch({ type: 'SET_STATUS', status: 'connecting' })
      const detail = await analysisApi.getSession(sessionId)
      if (detail.status === 'completed' && detail.synthesisContent) {
        const restoredAgents: AgentResult[] = (detail.agentResults ?? []).map((ar) => ({
          agent: ar.agent,
          summary: ar.summary,
          keyInsights: ar.keyInsights ?? [],
        }))
        const restoredStatuses: Record<string, { status: AgentStatus['status']; latencyMs?: number }> = {}
        for (const ar of detail.agentResults ?? []) {
          const entry: { status: AgentStatus['status']; latencyMs?: number } = {
            status: 'complete' as const,
          }
          if (ar.latencyMs != null) {
            entry.latencyMs = ar.latencyMs
          }
          restoredStatuses[ar.agent] = entry
        }

        dispatch({
          type: 'RESTORE_SESSION',
          agentResults: restoredAgents,
          synthesisContent: detail.synthesisContent,
          clarificationRound: detail.clarificationRounds,
          agentStatuses: restoredStatuses,
        })

        // Write to React Query cache so it survives tab switches
        queryClient.setQueryData<CachedAnalysisResult>(['analysis-result', symbol], {
          agentResults: restoredAgents,
          synthesisContent: detail.synthesisContent,
          clarificationRound: detail.clarificationRounds,
          agentStatuses: restoredStatuses,
          completedAt: Date.now(),
        })
      } else if (detail.status === 'failed') {
        dispatch({ type: 'ERROR', error: detail.error ?? t('analysis.errorFailed') })
      }
    } catch (err) {
      dispatch({ type: 'RESET' })
      console.error('Failed to restore analysis session:', err)
    }
  }, [symbol, queryClient, t])

  const backToHistory = useCallback(() => {
    dispatch({ type: 'BACK_TO_HISTORY' })
    queryClient.removeQueries({ queryKey: ['analysis-result', symbol] })
    queryClient.invalidateQueries({ queryKey: ['analysis-sessions', symbol] })
  }, [symbol, queryClient])

  const handleDeleteSession = useCallback(async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    try {
      await analysisApi.deleteSession(sessionId)
      queryClient.invalidateQueries({ queryKey: ['analysis-sessions', symbol] })
    } catch {
      // Silently ignore -- session may already be deleted
    }
  }, [symbol, queryClient])

  // ---------------------------------------------------------------------------
  // Render helpers
  // ---------------------------------------------------------------------------

  const getStatusIcon = () => {
    switch (state.status) {
      case 'connecting':
      case 'analyzing':
      case 'synthesizing':
        return <Loader2 className="h-4 w-4 animate-spin" />
      case 'complete':
        return <CheckCircle2 className="h-4 w-4 text-stock-up" />
      case 'error':
        return <AlertCircle className="h-4 w-4 text-destructive" />
      default:
        return <Brain className="h-4 w-4" />
    }
  }

  const getAgentStatusIcon = (agentStatus: AgentStatus['status']) => {
    switch (agentStatus) {
      case 'running':
        return <Loader2 className="h-3 w-3 animate-spin text-primary" />
      case 'complete':
        return <CheckCircle2 className="h-3 w-3 text-stock-up" />
      case 'error':
        return <AlertCircle className="h-3 w-3 text-destructive" />
      default:
        return null
    }
  }

  const agentLabelMap: Record<string, string> = {
    fundamental: t('analysis.agentFundamental'),
    technical: t('analysis.agentTechnical'),
    sentiment: t('analysis.agentSentiment'),
    news: t('analysis.agentNews'),
  }

  /**
   * Filter out JSON code blocks from synthesis content.
   * The LLM outputs structured JSON at the end for machine parsing.
   */
  const filterJsonBlocks = (content: string): string => {
    let filtered = content.replace(/```json[\s\S]*?```/g, '')
    filtered = filtered.replace(/```json[\s\S]*$/g, '')
    filtered = filtered.replace(
      /\n*(?:结构化机器可解析数据|structured data|After your Markdown analysis)[：:.]?\s*$/gi,
      ''
    )
    return filtered.trim()
  }

  const renderAgentResults = () => {
    if (state.agentResults.length === 0) return null

    return (
      <div className="space-y-2 mb-4">
        {state.agentResults.map((result) => (
          <details
            key={result.agent}
            className="rounded-lg border bg-card"
            open={state.status !== 'complete'}
          >
            <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer text-sm font-medium hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-lg">
              <CheckCircle2 className="h-3.5 w-3.5 text-stock-up flex-shrink-0" />
              {agentLabelMap[result.agent] ?? result.agent}
            </summary>
            <div className="px-3 pb-3 pt-1 text-sm">
              <p className="text-muted-foreground leading-relaxed">
                {result.summary}
              </p>
              {result.keyInsights && result.keyInsights.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {result.keyInsights.map((insight, idx) => (
                    <li key={idx} className="flex items-start gap-2 text-xs">
                      <span
                        className={cn(
                          'mt-1.5 h-1.5 w-1.5 rounded-full flex-shrink-0',
                          { high: 'bg-destructive', medium: 'bg-primary' }[insight.importance] ?? 'bg-muted-foreground'
                        )}
                      />
                      <div>
                        <span className="font-medium">{insight.title}:</span>{' '}
                        <span className="text-muted-foreground">
                          {insight.description}
                        </span>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </details>
        ))}
      </div>
    )
  }

  const renderSynthesisContent = () => {
    if (!state.synthesisContent) {
      if (state.status === 'idle') {
        const sessions = pastSessionsQuery.data
        if (sessions && sessions.length > 0) {
          return (
            <div className="space-y-3 py-4">
              <p className="text-sm text-muted-foreground">
                {t('analysis.historyTitle')}
              </p>
              <div className="space-y-2">
                {sessions.map((s) => (
                  <div
                    key={s.id}
                    className="group flex w-full items-center justify-between rounded-lg border p-3 text-left transition-colors hover:bg-muted/50 cursor-pointer"
                    role="button"
                    tabIndex={0}
                    onClick={() => restoreSession(s.id)}
                    onKeyDown={(e) => { if (e.key === 'Enter') restoreSession(s.id) }}
                  >
                    <div className="flex items-center gap-2">
                      {s.status === 'completed' ? (
                        <CheckCircle2 className="h-4 w-4 text-green-500" />
                      ) : (
                        <XCircle className="h-4 w-4 text-destructive" />
                      )}
                      <span className="text-sm font-medium">{s.symbol}</span>
                    </div>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Clock className="h-3 w-3" />
                      <span>{formatRelativeTime(s.completedAt ?? s.createdAt)}</span>
                      <button
                        type="button"
                        className="ml-1 rounded p-1 opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                        onClick={(e) => handleDeleteSession(s.id, e)}
                        title={t('analysis.deleteSession')}
                        aria-label={t('analysis.deleteSession')}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        }
        return (
          <div className="flex min-h-[200px] items-center justify-center text-muted-foreground py-8">
            <p>{t('analysis.idlePrompt')}</p>
          </div>
        )
      }
      return null
    }

    const displayContent = filterJsonBlocks(state.synthesisContent)

    return (
      <div className="space-y-4">
        <div className="prose prose-sm dark:prose-invert max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {displayContent}
          </ReactMarkdown>
        </div>
        {(state.status === 'analyzing' || state.status === 'synthesizing') && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="text-xs">{t('analysis.btnGenerating')}</span>
          </div>
        )}
      </div>
    )
  }

  const isStreaming = state.status === 'connecting' || state.status === 'analyzing' || state.status === 'synthesizing'

  return (
    <Card className={cn('flex flex-col', className)}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {getStatusIcon()}
            <CardTitle className="text-lg">{t('analysis.title')}</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            {state.status === 'complete' && (
              <Button variant="ghost" size="sm" onClick={backToHistory}>
                <ArrowLeft className="mr-1 h-4 w-4" />
                {t('analysis.btnHistory')}
              </Button>
            )}
            {isStreaming ? (
              <Button variant="outline" size="sm" onClick={cancelAnalysis}>
                {t('analysis.btnCancel')}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => {
                  startAnalysis(state.status === 'complete' ? { forceNew: true } : undefined)
                }}
                disabled={isStreaming}
              >
                {state.status === 'complete' ? (
                  <>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    {t('analysis.btnReanalyze')}
                  </>
                ) : (
                  <>
                    <Brain className="mr-2 h-4 w-4" />
                    {t('analysis.btnAnalyze')}
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
        <CardDescription>
          {symbol} - {t('analysis.description')}
          {state.clarificationRound > 0 && (
            <span className="ml-2 inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium">
              {t('analysis.clarificationBadge', { count: state.clarificationRound })}
            </span>
          )}
        </CardDescription>

        {/* Agent Status Indicators */}
        {state.status !== 'idle' && (
          <div className="flex flex-wrap gap-2 mt-3">
            {Object.entries(state.agents).map(([key, agent]) => {
              const AgentIcon = AGENT_ICONS[key] ?? Brain
              return (
                <div
                  key={key}
                  className={cn(
                    'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs',
                    agent.status === 'idle' && 'bg-muted text-muted-foreground',
                    agent.status === 'running' && 'bg-primary/10 text-primary',
                    agent.status === 'complete' && 'bg-stock-up/10 text-stock-up',
                    agent.status === 'error' && 'bg-destructive/10 text-destructive'
                  )}
                >
                  <AgentIcon className="h-3 w-3" />
                  <span>{agentLabelMap[key] ?? key}</span>
                  {getAgentStatusIcon(agent.status)}
                  {agent.latencyMs != null && agent.status === 'complete' && (
                    <span className="text-muted-foreground">({(agent.latencyMs / 1000).toFixed(1)}s)</span>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {state.progress && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            {state.progress}
          </div>
        )}
        {state.error && (
          <div role="alert" className="flex items-center gap-2 text-sm text-destructive mt-2">
            <AlertCircle className="h-3 w-3" />
            {state.error}
          </div>
        )}
      </CardHeader>

      <CardContent className="flex-1 pt-0">
        <div className="pr-4">
          {renderAgentResults()}
          {renderSynthesisContent()}
        </div>
      </CardContent>
    </Card>
  )
}
