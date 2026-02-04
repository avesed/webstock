import { Bot, User } from 'lucide-react'

import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/lib/utils'
import type { ChatMessage } from '@/types'

interface ChatMessageBubbleProps {
  readonly message: ChatMessage
  readonly isStreaming?: boolean
}

/**
 * Renders basic markdown formatting for assistant messages.
 * Supports paragraphs, code blocks, bold, italic, and source references.
 */
function renderMarkdown(content: string): React.ReactNode[] {
  const blocks: React.ReactNode[] = []
  const lines = content.split('\n')
  let currentBlock: string[] = []
  let inCodeBlock = false
  let codeLanguage = ''
  let codeLines: string[] = []

  const flushParagraph = () => {
    if (currentBlock.length > 0) {
      const text = currentBlock.join('\n')
      if (text.trim()) {
        blocks.push(
          <p key={blocks.length} className="mb-2 last:mb-0 leading-relaxed">
            {renderInlineFormatting(text)}
          </p>
        )
      }
      currentBlock = []
    }
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!

    if (line.startsWith('```')) {
      if (inCodeBlock) {
        // End code block
        blocks.push(
          <div key={blocks.length} className="mb-2 last:mb-0">
            {codeLanguage && (
              <div className="rounded-t-md bg-muted/80 px-3 py-1 text-[10px] font-mono text-muted-foreground">
                {codeLanguage}
              </div>
            )}
            <pre
              className={cn(
                'overflow-x-auto bg-muted/50 p-3 font-mono text-xs leading-relaxed',
                codeLanguage ? 'rounded-b-md' : 'rounded-md'
              )}
            >
              <code>{codeLines.join('\n')}</code>
            </pre>
          </div>
        )
        inCodeBlock = false
        codeLines = []
        codeLanguage = ''
      } else {
        // Start code block
        flushParagraph()
        inCodeBlock = true
        codeLanguage = line.slice(3).trim()
      }
    } else if (inCodeBlock) {
      codeLines.push(line)
    } else if (line.trim() === '') {
      flushParagraph()
    } else if (line.startsWith('# ')) {
      flushParagraph()
      blocks.push(
        <h3 key={blocks.length} className="mb-2 text-base font-bold">
          {renderInlineFormatting(line.slice(2))}
        </h3>
      )
    } else if (line.startsWith('## ')) {
      flushParagraph()
      blocks.push(
        <h4 key={blocks.length} className="mb-2 text-sm font-bold">
          {renderInlineFormatting(line.slice(3))}
        </h4>
      )
    } else if (line.startsWith('- ') || line.startsWith('* ')) {
      flushParagraph()
      blocks.push(
        <li key={blocks.length} className="mb-1 ml-4 list-disc text-sm last:mb-0">
          {renderInlineFormatting(line.slice(2))}
        </li>
      )
    } else if (/^\d+\.\s/.test(line)) {
      flushParagraph()
      const matchResult = line.match(/^\d+\.\s(.*)/)
      blocks.push(
        <li key={blocks.length} className="mb-1 ml-4 list-decimal text-sm last:mb-0">
          {renderInlineFormatting(matchResult?.[1] ?? line)}
        </li>
      )
    } else {
      currentBlock.push(line)
    }
  }

  // Flush remaining content
  if (inCodeBlock && codeLines.length > 0) {
    blocks.push(
      <pre key={blocks.length} className="mb-2 overflow-x-auto rounded-md bg-muted/50 p-3 font-mono text-xs leading-relaxed last:mb-0">
        <code>{codeLines.join('\n')}</code>
      </pre>
    )
  }
  flushParagraph()

  return blocks
}

function renderInlineFormatting(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  // Match bold, italic, inline code, and source references
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[Source\s+(\d+)\])/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(text)) !== null) {
    // Push text before match
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index))
    }

    if (match[1]) {
      // Bold
      nodes.push(
        <strong key={`b-${match.index}`} className="font-semibold">
          {match[2]}
        </strong>
      )
    } else if (match[3]) {
      // Italic
      nodes.push(
        <em key={`i-${match.index}`}>
          {match[4]}
        </em>
      )
    } else if (match[5]) {
      // Inline code
      nodes.push(
        <code key={`c-${match.index}`} className="rounded bg-muted/70 px-1 py-0.5 font-mono text-xs">
          {match[6]}
        </code>
      )
    } else if (match[7]) {
      // Source reference
      nodes.push(
        <span
          key={`s-${match.index}`}
          className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-primary/20 text-[10px] font-medium text-primary align-text-top ml-0.5"
        >
          {match[8]}
        </span>
      )
    }

    lastIndex = match.index + match[0].length
  }

  // Push remaining text
  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex))
  }

  return nodes.length > 0 ? nodes : [text]
}

export function ChatMessageBubble({ message, isStreaming = false }: ChatMessageBubbleProps) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {/* Assistant avatar */}
      {!isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
          <Bot className="h-4 w-4 text-muted-foreground" />
        </div>
      )}

      <div className={cn('flex max-w-[80%] flex-col', isUser ? 'items-end' : 'items-start')}>
        {/* Message bubble */}
        <div
          className={cn(
            'rounded-2xl px-4 py-2.5 text-sm',
            isUser
              ? 'bg-primary text-primary-foreground rounded-br-md'
              : 'bg-muted rounded-bl-md'
          )}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap break-words">{message.content}</p>
          ) : (
            <div className="min-w-0 break-words">
              {renderMarkdown(message.content)}
              {isStreaming && (
                <span className="inline-block h-4 w-1.5 animate-pulse bg-foreground/70 ml-0.5 align-text-bottom rounded-sm" />
              )}
            </div>
          )}
        </div>

        {/* Timestamp */}
        <span className="mt-1 text-[10px] text-muted-foreground/60 px-1">
          {formatRelativeTime(message.createdAt)}
        </span>
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <User className="h-4 w-4" />
        </div>
      )}
    </div>
  )
}
