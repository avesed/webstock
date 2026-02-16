import { useRef, useState, useEffect, type ReactNode } from 'react'
import { Trash2 } from 'lucide-react'
import { useDrag } from '@use-gesture/react'
import { useTranslation } from 'react-i18next'
import { useIsMobile } from '@/hooks/useIsMobile'
import { useToast } from '@/hooks'
import { ToastAction } from '@/components/ui/toast'

interface SwipeableItemProps {
  onDelete: () => void
  children: ReactNode
  disabled?: boolean
}

const DELETE_THRESHOLD = -80

export function SwipeableItem({ onDelete, children, disabled }: SwipeableItemProps) {
  const isMobile = useIsMobile()
  const { t } = useTranslation('common')
  const { toast } = useToast()
  const containerRef = useRef<HTMLDivElement>(null)
  const [offsetX, setOffsetX] = useState(0)
  const [isRemoving, setIsRemoving] = useState(false)
  const undoCancelledRef = useRef(false)
  const deleteTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Cleanup timeout on unmount to prevent state updates on unmounted component
  useEffect(() => {
    return () => {
      if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current)
    }
  }, [])

  useDrag(
    ({ active, movement: [mx], cancel }) => {
      if (disabled) {
        cancel()
        return
      }
      if (active) {
        // Only allow left swipe (negative)
        const clamped = Math.min(0, mx)
        setOffsetX(clamped)
      } else {
        if (mx < DELETE_THRESHOLD) {
          // Past threshold -- trigger delete with undo
          setIsRemoving(true)
          setOffsetX(0)
          undoCancelledRef.current = false

          toast({
            title: t('pwa.itemDeleted'),
            description: t('pwa.swipeToDelete'),
            duration: 3000,
            action: (
              <ToastAction
                altText={t('pwa.undoDelete')}
                onClick={() => {
                  undoCancelledRef.current = true
                  setIsRemoving(false)
                }}
              >
                {t('pwa.undoDelete')}
              </ToastAction>
            ),
          })

          // Wait 3 seconds for undo, then delete (matches toast duration)
          if (deleteTimerRef.current) clearTimeout(deleteTimerRef.current)
          deleteTimerRef.current = setTimeout(() => {
            if (!undoCancelledRef.current) {
              onDelete()
            }
          }, 3000)
        } else {
          // Spring back
          setOffsetX(0)
        }
      }
    },
    {
      target: containerRef,
      filterTaps: true,
      axis: 'x',
      enabled: isMobile && !disabled && !isRemoving,
    }
  )

  // On desktop, render children directly
  if (!isMobile) {
    return <>{children}</>
  }

  if (isRemoving) {
    return null
  }

  const revealWidth = Math.abs(Math.min(offsetX, 0))
  const revealOpacity = Math.min(revealWidth / Math.abs(DELETE_THRESHOLD), 1)

  return (
    <div ref={containerRef} className="relative overflow-hidden" style={{ touchAction: 'pan-y' }}>
      {/* Red background revealed behind */}
      <div
        className="absolute inset-y-0 right-0 flex items-center justify-center bg-destructive"
        style={{
          width: `${revealWidth}px`,
          opacity: revealOpacity,
        }}
      >
        <Trash2 className="h-5 w-5 text-destructive-foreground" />
      </div>

      {/* Sliding content */}
      <div
        style={{
          transform: offsetX < 0 ? `translateX(${offsetX}px)` : undefined,
          transition: offsetX === 0 ? 'transform 0.3s ease-out' : 'none',
        }}
      >
        {children}
      </div>
    </div>
  )
}
