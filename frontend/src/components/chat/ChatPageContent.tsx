import { useState, useEffect } from 'react'
import { ChevronLeft } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useChatStore } from '@/stores/chatStore'
import { ConversationList } from './ConversationList'
import { ChatArea } from './ChatArea'

export function ChatPageContent() {
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

  return (
    <div className="flex h-[calc(100%+2rem)] lg:h-[calc(100%+3rem)] -m-4 lg:-m-6">
      {/* Conversation sidebar */}
      <div
        className={cn(
          'w-full lg:w-80 border-r shrink-0 bg-card',
          showChat ? 'hidden lg:block' : 'block'
        )}
      >
        <ConversationList />
      </div>

      {/* Chat area */}
      <div
        className={cn(
          'flex-1 min-w-0 flex flex-col',
          showChat ? 'flex' : 'hidden lg:flex'
        )}
      >
        {/* Mobile back button */}
        <div className="flex items-center border-b px-2 py-1.5 lg:hidden">
          <Button variant="ghost" size="sm" className="gap-1" onClick={handleBack}>
            <ChevronLeft className="h-4 w-4" />
            Back
          </Button>
        </div>

        <div className="flex-1 min-h-0">
          <ChatArea />
        </div>
      </div>
    </div>
  )
}
