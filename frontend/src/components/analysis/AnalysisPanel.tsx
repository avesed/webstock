import { useState, useCallback, useRef, useEffect } from 'react'
import DOMPurify from 'dompurify'
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
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { getAccessToken } from '@/lib/auth'
import { useLocale } from '@/hooks/useLocale'

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

interface SSEEvent {
  type:
    | 'heartbeat'
    | 'start'
    | 'analysis_phase_start'
    | 'agent_start'
    | 'agent_complete'
    | 'analysis_phase_complete'
    | 'synthesis_start'
    | 'synthesis_chunk'
    | 'clarification_needed'
    | 'clarification_start'
    | 'clarification_complete'
    | 'complete'
    | 'timeout'
    | 'error'
  agent?: string
  content?: string
  message?: string
  error?: string
  success?: boolean
  latency_ms?: number
  synthesis_output?: string
  agents_completed?: number
  timestamp?: number
}

type StreamStatus = 'idle' | 'connecting' | 'analyzing' | 'synthesizing' | 'complete' | 'error'

const createInitialAgentStatus = (): Record<string, AgentStatus> => ({
  fundamental: { name: 'Fundamental', icon: BarChart3, status: 'idle' },
  technical: { name: 'Technical', icon: TrendingUp, status: 'idle' },
  sentiment: { name: 'Sentiment', icon: MessageSquare, status: 'idle' },
  news: { name: 'News', icon: Newspaper, status: 'idle' },
})

export default function AnalysisPanel({ symbol, className }: AnalysisPanelProps) {
  const { locale } = useLocale()
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string>('')
  const [agents, setAgents] = useState<Record<string, AgentStatus>>(createInitialAgentStatus)
  const [synthesisContent, setSynthesisContent] = useState<string>('')
  const [clarificationRound, setClarificationRound] = useState<number>(0)
  const abortControllerRef = useRef<AbortController | null>(null)

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  // Reset state when symbol changes
  useEffect(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setStatus('idle')
    setError(null)
    setProgress('')
    setAgents(createInitialAgentStatus())
    setSynthesisContent('')
    setClarificationRound(0)
  }, [symbol])

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    switch (event.type) {
      case 'heartbeat':
        // Ignore heartbeat events
        break

      case 'start':
      case 'analysis_phase_start':
        setStatus('analyzing')
        setProgress('Analyzing with AI agents...')
        break

      case 'agent_start':
        if (event.agent && event.agent in agents) {
          const agentKey = event.agent as keyof typeof agents
          setAgents((prev) => {
            const current = prev[agentKey]
            if (!current) return prev
            const updated: AgentStatus = {
              name: current.name,
              icon: current.icon,
              status: 'running',
            }
            return { ...prev, [agentKey]: updated }
          })
        }
        break

      case 'agent_complete':
        if (event.agent && event.agent in agents) {
          const agentKey = event.agent as keyof typeof agents
          setAgents((prev) => {
            const current = prev[agentKey]
            if (!current) return prev
            const updated: AgentStatus = {
              name: current.name,
              icon: current.icon,
              status: event.success ? 'complete' : 'error',
            }
            if (typeof event.latency_ms === 'number') {
              updated.latencyMs = event.latency_ms
            }
            return { ...prev, [agentKey]: updated }
          })
        }
        break

      case 'analysis_phase_complete':
        setProgress('Synthesizing results...')
        break

      case 'synthesis_start':
        setStatus('synthesizing')
        setProgress('Generating synthesis...')
        break

      case 'synthesis_chunk':
        if (event.content) {
          setSynthesisContent((prev) => prev + event.content)
        }
        break

      case 'clarification_needed':
        setClarificationRound((prev) => prev + 1)
        setProgress('Clarifying analysis...')
        break

      case 'clarification_start':
        setProgress('Running clarification round...')
        break

      case 'clarification_complete':
        setProgress('Clarification complete, refining synthesis...')
        break

      case 'complete':
        setStatus('complete')
        setProgress('')
        // Use final synthesis_output if provided
        if (event.synthesis_output) {
          setSynthesisContent(event.synthesis_output)
        }
        break

      case 'timeout':
        setError('Analysis timeout. Please try again.')
        setStatus('error')
        break

      case 'error':
        setError(event.error ?? event.message ?? 'An error occurred during analysis')
        setStatus('error')
        break
    }
  }, [])

  const startAnalysis = useCallback(async () => {
    // Cancel any existing stream
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
    }

    // Reset state
    setStatus('connecting')
    setError(null)
    setProgress('Initializing analysis...')
    setAgents(createInitialAgentStatus())
    setSynthesisContent('')
    setClarificationRound(0)

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    try {
      const token = getAccessToken()
      const lang = locale.toLowerCase().startsWith('zh') ? 'zh' : 'en'

      // Use v2 endpoint (LangGraph)
      const response = await fetch(`/api/v1/analysis/${symbol}/stream/v2?language=${lang}`, {
        method: 'GET',
        headers: {
          Accept: 'text/event-stream',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        credentials: 'include',
        signal: abortController.signal,
      })

      if (!response.ok) {
        throw new Error(`Analysis failed: ${response.statusText}`)
      }

      const reader = response.body?.getReader()
      if (!reader) {
        throw new Error('Failed to get response reader')
      }

      setStatus('analyzing')
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()

        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (data) {
              try {
                const event: SSEEvent = JSON.parse(data)
                handleSSEEvent(event)
              } catch {
                // Skip invalid JSON
              }
            }
          }
        }
      }

      // Ensure completion status is set
      if (status !== 'error') {
        setStatus('complete')
      }
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        return
      }
      setStatus('error')
      setError(err instanceof Error ? err.message : 'Analysis failed')
    }
  }, [symbol, locale, handleSSEEvent, status])

  const cancelAnalysis = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setStatus('idle')
    setProgress('')
  }, [])

  const getStatusIcon = () => {
    switch (status) {
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

  const renderSynthesisContent = () => {
    if (!synthesisContent) {
      if (status === 'idle') {
        return (
          <div className="flex min-h-[200px] items-center justify-center text-muted-foreground py-8">
            <p>Click "Analyze" to generate AI-powered comprehensive analysis</p>
          </div>
        )
      }
      return null
    }

    const displayContent = filterJsonBlocks(synthesisContent)

    return (
      <div className="space-y-4">
        <div className="prose prose-sm dark:prose-invert max-w-none">
          {displayContent.split('\n').map((line, index) => {
            if (!line.trim()) return <br key={index} />

            // Handle headers
            if (line.startsWith('###')) {
              return (
                <h4 key={index} className="text-base font-semibold mt-4 mb-2">
                  {line.replace(/^###\s*/, '')}
                </h4>
              )
            }
            if (line.startsWith('##')) {
              return (
                <h3 key={index} className="text-lg font-semibold mt-4 mb-2">
                  {line.replace(/^##\s*/, '')}
                </h3>
              )
            }
            if (line.startsWith('#')) {
              return (
                <h2 key={index} className="text-xl font-bold mt-4 mb-2">
                  {line.replace(/^#\s*/, '')}
                </h2>
              )
            }

            // Handle bullet points
            if (line.startsWith('- ') || line.startsWith('* ')) {
              return (
                <li key={index} className="ml-4">
                  {line.replace(/^[-*]\s*/, '')}
                </li>
              )
            }

            // Handle bold text with XSS sanitization
            const formattedLine = line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
            const sanitizedLine = DOMPurify.sanitize(formattedLine, {
              ALLOWED_TAGS: ['strong', 'em', 'b', 'i'],
              ALLOWED_ATTR: [],
            })

            return (
              <p
                key={index}
                className="leading-relaxed"
                dangerouslySetInnerHTML={{ __html: sanitizedLine }}
              />
            )
          })}
        </div>
        {(status === 'analyzing' || status === 'synthesizing') && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="text-xs">Generating...</span>
          </div>
        )}
      </div>
    )
  }

  const isStreaming = status === 'connecting' || status === 'analyzing' || status === 'synthesizing'

  return (
    <Card className={cn('flex flex-col', className)}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {getStatusIcon()}
            <CardTitle className="text-lg">AI Analysis</CardTitle>
          </div>
          <div className="flex items-center gap-2">
            {isStreaming ? (
              <Button variant="outline" size="sm" onClick={cancelAnalysis}>
                Cancel
              </Button>
            ) : (
              <Button size="sm" onClick={startAnalysis} disabled={isStreaming}>
                {status === 'complete' ? (
                  <>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Re-analyze
                  </>
                ) : (
                  <>
                    <Brain className="mr-2 h-4 w-4" />
                    Analyze
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
        <CardDescription>
          {symbol} - Comprehensive AI-powered stock analysis
          {clarificationRound > 0 && (
            <span className="ml-2 inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium">
              +{clarificationRound} clarification
            </span>
          )}
        </CardDescription>

        {/* Agent Status Indicators */}
        {status !== 'idle' && (
          <div className="flex flex-wrap gap-2 mt-3">
            {Object.entries(agents).map(([key, agent]) => (
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
                <agent.icon className="h-3 w-3" />
                <span>{agent.name}</span>
                {getAgentStatusIcon(agent.status)}
                {agent.latencyMs && agent.status === 'complete' && (
                  <span className="text-muted-foreground">({(agent.latencyMs / 1000).toFixed(1)}s)</span>
                )}
              </div>
            ))}
          </div>
        )}

        {progress && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground mt-2">
            <Loader2 className="h-3 w-3 animate-spin" />
            {progress}
          </div>
        )}
        {error && (
          <div className="flex items-center gap-2 text-sm text-destructive mt-2">
            <AlertCircle className="h-3 w-3" />
            {error}
          </div>
        )}
      </CardHeader>

      <CardContent className="flex-1 pt-0">
        <div className="pr-4">{renderSynthesisContent()}</div>
      </CardContent>
    </Card>
  )
}
