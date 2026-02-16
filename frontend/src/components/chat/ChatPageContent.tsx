import { useState, useEffect, useRef } from 'react'
import { ChevronLeft } from 'lucide-react'
import { useDrag } from '@use-gesture/react'
import { useTranslation } from 'react-i18next'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useChatStore } from '@/stores/chatStore'
import { useIsMobile } from '@/hooks/useIsMobile'
import { ConversationList } from './ConversationList'
import { ChatArea } from './ChatArea'

export function ChatPageContent() {
  const { t } = useTranslation('common')
  const currentConversationId = useChatStore((s) => s.currentConversationId)
  const [showChat, setShowChat] = useState(false)

  // On mobile, switch to chat view when a conversation is selected
  useEffect(() => {
    if (currentConversationId) {
      setShowChat(true)
    }
  }, [currentConversationId])

  const handleBack = () => {
    setShowChat(false)
  }

  const isMobile = useIsMobile()
  const chatPanelRef = useRef<HTMLDivElement>(null)
  const [swipeOffset, setSwipeOffset] = useState(0)

  useDrag(
    ({ active, movement: [mx], velocity: [vx], direction: [dx], cancel }) => {
      if (!showChat || !isMobile) {
        cancel()
        return
      }
      if (active) {
        // Only allow right swipe (positive direction)
        const offset = Math.max(0, mx)
        setSwipeOffset(offset)
      } else {
        // Check if swipe was strong enough
        if (mx > 80 && vx > 0.3 && dx > 0) {
          handleBack()
        }
        setSwipeOffset(0)
      }
    },
    {
      target: chatPanelRef,
      filterTaps: true,
      axis: 'x',
      enabled: showChat && isMobile,
    }
  )

  const swipeOpacity = swipeOffset > 0 ? Math.max(1 - swipeOffset / 200, 0.3) : 1

  // Calculate height: viewport - header(64px) - breadcrumb(~36px) - main padding(16px top + 16px bottom on mobile, 24px on lg)
  // Mobile: 100vh - 64px - 36px - 32px = 100vh - 132px
  // Desktop: 100vh - 64px - 36px - 48px = 100vh - 148px
  return (
    <div className="flex h-[calc(100dvh-188px)] lg:h-[calc(100vh-148px)] -m-4 lg:-m-6">
      {/* Conversation sidebar */}
      <div
        className={cn(
          'w-full lg:w-80 border-r shrink-0 bg-card flex flex-col overflow-hidden',
          showChat ? 'hidden lg:flex' : 'flex'
        )}
      >
        <ConversationList />
      </div>

      {/* Chat area */}
      <div
        ref={chatPanelRef}
        className={cn(
          'flex-1 min-w-0 flex flex-col overflow-hidden',
          showChat ? 'flex' : 'hidden lg:flex'
        )}
        style={{
          transform: swipeOffset > 0 ? `translateX(${swipeOffset}px)` : undefined,
          opacity: swipeOpacity,
          transition: swipeOffset > 0 ? 'none' : 'transform 0.3s ease-out, opacity 0.3s ease-out',
          touchAction: 'pan-y',
        }}
      >
        {/* Mobile back button */}
        <div className="flex items-center border-b px-2 py-1.5 lg:hidden shrink-0">
          <Button variant="ghost" size="sm" className="gap-1" onClick={handleBack}>
            <ChevronLeft className="h-4 w-4" />
            {t('actions.back')}
          </Button>
        </div>

        <div className="flex-1 min-h-0 overflow-hidden">
          <ChatArea />
        </div>
      </div>
    </div>
  )
}
