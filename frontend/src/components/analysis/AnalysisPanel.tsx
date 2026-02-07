import { useState, useCallback, useRef, useEffect } from 'react'
import DOMPurify from 'dompurify'
import {
  Brain,
  TrendingUp,
  BarChart3,
  MessageSquare,
  Loader2,
  AlertCircle,
  RefreshCw,
  CheckCircle2,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { getAccessToken } from '@/lib/auth'
import { useLocale } from '@/hooks/useLocale'
import type { AnalysisType } from '@/types'

interface AnalysisPanelProps {
  symbol: string
  className?: string
}

interface AnalysisSection {
  type: AnalysisType
  content: string
  isLoading: boolean
  isComplete: boolean
}

interface AnalysisSections {
  fundamental: AnalysisSection
  technical: AnalysisSection
  sentiment: AnalysisSection
}

type AgentName = 'fundamental' | 'technical' | 'sentiment' | 'news'

interface SSEEvent {
  type: 'heartbeat' | 'start' | 'agent_start' | 'agent_chunk' | 'agent_complete' | 'agent_error' | 'complete' | 'timeout' | 'error'
  agent?: AgentName
  content?: string
  message?: string
  error?: string
  structured_data?: Record<string, unknown>
  symbol?: string
  agents?: string[]
}

type StreamStatus = 'idle' | 'connecting' | 'streaming' | 'complete' | 'error'

const createInitialSections = (): AnalysisSections => ({
  fundamental: { type: 'FUNDAMENTAL', content: '', isLoading: false, isComplete: false },
  technical: { type: 'TECHNICAL', content: '', isLoading: false, isComplete: false },
  sentiment: { type: 'SENTIMENT', content: '', isLoading: false, isComplete: false },
})

export default function AnalysisPanel({ symbol, className }: AnalysisPanelProps) {
  const { locale } = useLocale()
  const [status, setStatus] = useState<StreamStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string>('')
  const [sections, setSections] = useState<AnalysisSections>(createInitialSections)
  const [activeTab, setActiveTab] = useState<string>('fundamental')
  const abortControllerRef = useRef<AbortController | null>(null)
  const statusRef = useRef<StreamStatus>('idle')

  // Keep status ref in sync
  useEffect(() => {
    statusRef.current = status
  }, [status])

  // Clean up on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [])

  // Reset state and abort in-flight requests when symbol changes
  useEffect(() => {
    // Abort any in-flight analysis request when symbol changes
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      abortControllerRef.current = null
    }
    setStatus('idle')
    setError(null)
    setProgress('')
    setSections(createInitialSections())
  }, [symbol])

  const handleSSEEvent = useCallback((event: SSEEvent) => {
    switch (event.type) {
      case 'heartbeat':
        // Ignore heartbeat events - they're just keep-alive signals
        break

      case 'start':
        setProgress('Analyzing in parallel...')
        break

      case 'agent_start':
        // An agent is starting to analyze (parallel execution - don't switch tabs)
        if (event.agent && event.agent !== 'news') {
          const agentKey = event.agent as keyof AnalysisSections
          setSections((prev) => ({
            ...prev,
            [agentKey]: {
              ...prev[agentKey],
              isLoading: true,
            },
          }))
        }
        break

      case 'agent_chunk':
        // Streaming content chunk from an agent
        if (event.agent && event.agent !== 'news' && event.content) {
          const agentKey = event.agent as keyof AnalysisSections
          setSections((prev) => ({
            ...prev,
            [agentKey]: {
              ...prev[agentKey],
              content: prev[agentKey].content + event.content,
            },
          }))
        }
        break

      case 'agent_complete':
        // An agent finished its analysis
        if (event.agent && event.agent !== 'news') {
          const agentKey = event.agent as keyof AnalysisSections
          setSections((prev) => ({
            ...prev,
            [agentKey]: {
              ...prev[agentKey],
              isLoading: false,
              isComplete: true,
            },
          }))
        }
        break

      case 'agent_error':
        // An individual agent encountered an error
        if (event.agent && event.agent !== 'news') {
          const agentKey = event.agent as keyof AnalysisSections
          setSections((prev) => ({
            ...prev,
            [agentKey]: {
              ...prev[agentKey],
              isLoading: false,
              isComplete: true,
              content: prev[agentKey].content || `Error: ${event.error ?? 'Analysis failed'}`,
            },
          }))
        }
        break

      case 'complete':
        // All agents have completed
        setProgress('')
        setSections((prev) => ({
          fundamental: { ...prev.fundamental, isLoading: false, isComplete: true },
          technical: { ...prev.technical, isLoading: false, isComplete: true },
          sentiment: { ...prev.sentiment, isLoading: false, isComplete: true },
        }))
        setStatus('complete')
        break

      case 'timeout':
        setError('Analysis timeout. Please try again.')
        setStatus('error')
        break

      case 'error':
        setError(event.error ?? 'An error occurred during analysis')
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
    setSections(createInitialSections())

    const abortController = new AbortController()
    abortControllerRef.current = abortController

    try {
      const token = getAccessToken()
      // Normalize language: 'zh-CN', 'zh-TW' etc. → 'zh', others → 'en'
      const lang = locale.toLowerCase().startsWith('zh') ? 'zh' : 'en'
      const params = new URLSearchParams({
        types: 'FUNDAMENTAL,TECHNICAL,SENTIMENT',
        language: lang,
      })

      const response = await fetch(`/api/v1/analysis/${symbol}/stream?${params}`, {
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

      setStatus('streaming')
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

      setStatus('complete')
    } catch (err) {
      if (err instanceof Error && err.name === 'AbortError') {
        // User cancelled, don't show error
        return
      }
      setStatus('error')
      setError(err instanceof Error ? err.message : 'Analysis failed')
    }
  }, [symbol, locale, handleSSEEvent])

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
      case 'streaming':
        return <Loader2 className="h-4 w-4 animate-spin" />
      case 'complete':
        return <CheckCircle2 className="h-4 w-4 text-stock-up" />
      case 'error':
        return <AlertCircle className="h-4 w-4 text-destructive" />
      default:
        return <Brain className="h-4 w-4" />
    }
  }

  /**
   * Filter out JSON code blocks from analysis content.
   * The LLM outputs structured JSON at the end for machine parsing,
   * which should not be displayed to users.
   */
  const filterJsonBlocks = (content: string): string => {
    // Remove ```json ... ``` blocks (including partial/streaming blocks)
    let filtered = content.replace(/```json[\s\S]*?```/g, '')
    // Also remove unclosed ```json blocks (during streaming)
    filtered = filtered.replace(/```json[\s\S]*$/g, '')
    // Remove any trailing "结构化机器可解析数据" or similar headers before JSON
    filtered = filtered.replace(/\n*(?:结构化机器可解析数据|structured data|After your Markdown analysis)[：:.]?\s*$/gi, '')
    return filtered.trim()
  }

  const renderAnalysisContent = (content: string, isComplete: boolean) => {
    if (!content) {
      return (
        <div className="flex min-h-[120px] items-center justify-center text-muted-foreground py-8">
          <p>Click "Analyze" to generate AI analysis</p>
        </div>
      )
    }

    // Filter out JSON blocks before rendering
    const displayContent = filterJsonBlocks(content)

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
            const formattedLine = line.replace(
              /\*\*(.*?)\*\*/g,
              '<strong>$1</strong>'
            )
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
        {!isComplete && (status === 'streaming' || status === 'connecting') && (
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" />
            <span className="text-xs">Generating...</span>
          </div>
        )}
      </div>
    )
  }

  const tabConfigs = [
    {
      id: 'fundamental' as const,
      label: 'Fundamental',
      icon: BarChart3,
      description: 'Financial health and valuation analysis',
    },
    {
      id: 'technical' as const,
      label: 'Technical',
      icon: TrendingUp,
      description: 'Price trends and chart patterns',
    },
    {
      id: 'sentiment' as const,
      label: 'Sentiment',
      icon: MessageSquare,
      description: 'Market sentiment and news analysis',
    },
  ]

  const isStreaming = status === 'streaming' || status === 'connecting'

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
              <Button
                size="sm"
                onClick={startAnalysis}
                disabled={isStreaming}
              >
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
        </CardDescription>
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
        <Tabs value={activeTab} onValueChange={setActiveTab} className="h-full">
          <TabsList className="grid w-full grid-cols-3">
            {tabConfigs.map((tab) => (
              <TabsTrigger
                key={tab.id}
                value={tab.id}
                className="flex items-center gap-1.5"
              >
                <tab.icon className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">{tab.label}</span>
                {sections[tab.id].isLoading && (
                  <Loader2 className="h-3 w-3 animate-spin text-primary" />
                )}
                {sections[tab.id].isComplete && !sections[tab.id].isLoading && (
                  <CheckCircle2 className="h-3 w-3 text-stock-up" />
                )}
              </TabsTrigger>
            ))}
          </TabsList>

          {tabConfigs.map((tab) => (
            <TabsContent key={tab.id} value={tab.id} className="mt-4">
              <div className="pr-4">
                {renderAnalysisContent(
                  sections[tab.id].content,
                  sections[tab.id].isComplete
                )}
              </div>
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
    </Card>
  )
}
