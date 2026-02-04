import { Loader2, CheckCircle2, XCircle } from 'lucide-react'
import type { ToolCallStatus } from '@/types'

interface ToolCallIndicatorProps {
  toolCalls: ToolCallStatus[]
}

export function ToolCallIndicator({ toolCalls }: ToolCallIndicatorProps) {
  if (toolCalls.length === 0) return null

  return (
    <div className="flex flex-wrap gap-1.5 px-11 py-1">
      {toolCalls.map((tc) => (
        <span
          key={tc.id}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors ${
            tc.status === 'running'
              ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
              : tc.status === 'completed'
                ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
          }`}
        >
          {tc.status === 'running' && <Loader2 className="h-3 w-3 animate-spin" />}
          {tc.status === 'completed' && <CheckCircle2 className="h-3 w-3" />}
          {tc.status === 'failed' && <XCircle className="h-3 w-3" />}
          {tc.label}
        </span>
      ))}
    </div>
  )
}
