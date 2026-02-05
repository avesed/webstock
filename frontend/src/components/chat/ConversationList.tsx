import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, MessageSquare, Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { formatRelativeTime, truncate } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useChatStore } from '@/stores/chatStore'

export function ConversationList() {
  const { t } = useTranslation('chat')
  const conversations = useChatStore((s) => s.conversations)
  const currentConversationId = useChatStore((s) => s.currentConversationId)
  const isLoadingConversations = useChatStore((s) => s.isLoadingConversations)
  const loadConversations = useChatStore((s) => s.loadConversations)
  const selectConversation = useChatStore((s) => s.selectConversation)
  const createConversation = useChatStore((s) => s.createConversation)
  const deleteConversation = useChatStore((s) => s.deleteConversation)

  useEffect(() => {
    loadConversations()
  }, [loadConversations])

  const handleNewChat = async () => {
    await createConversation()
  }

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    await deleteConversation(id)
  }

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b px-4 py-3">
        <h2 className="text-sm font-semibold">{t('conversations')}</h2>
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={handleNewChat}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="p-2">
          {isLoadingConversations && conversations.length === 0 && (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}

          {!isLoadingConversations && conversations.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
              <MessageSquare className="h-8 w-8 text-muted-foreground/50 mb-2" />
              <p className="text-sm font-medium text-muted-foreground">{t('noConversations')}</p>
              <p className="text-xs text-muted-foreground/70 mt-1">
                {t('startFirst')}
              </p>
            </div>
          )}

          {conversations.map((conversation) => {
            const isActive = conversation.id === currentConversationId

            return (
              <div
                key={conversation.id}
                role="button"
                tabIndex={0}
                className={cn(
                  'group relative flex cursor-pointer flex-col gap-1 rounded-lg px-3 py-2.5 text-sm transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'hover:bg-accent'
                )}
                onClick={() => selectConversation(conversation.id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    selectConversation(conversation.id)
                  }
                }}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium leading-snug line-clamp-1">
                    {conversation.title ?? t('newConversation')}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn(
                      'h-6 w-6 shrink-0 opacity-0 transition-opacity group-hover:opacity-100',
                      isActive
                        ? 'hover:bg-primary-foreground/20 text-primary-foreground'
                        : 'hover:bg-destructive/10 text-muted-foreground hover:text-destructive'
                    )}
                    onClick={(e) => handleDelete(e, conversation.id)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
                {conversation.lastMessage && (
                  <span
                    className={cn(
                      'text-xs line-clamp-1',
                      isActive ? 'text-primary-foreground/70' : 'text-muted-foreground'
                    )}
                  >
                    {truncate(conversation.lastMessage, 60)}
                  </span>
                )}
                <span
                  className={cn(
                    'text-[10px]',
                    isActive ? 'text-primary-foreground/50' : 'text-muted-foreground/60'
                  )}
                >
                  {formatRelativeTime(conversation.updatedAt)}
                </span>
              </div>
            )
          })}
        </div>
      </ScrollArea>
    </div>
  )
}
