import { useRef, useState, useCallback, type ReactNode } from 'react'
import { Loader2 } from 'lucide-react'

interface PullToRefreshProps {
  onRefresh: () => Promise<void>
  children: ReactNode
  disabled?: boolean
}

const THRESHOLD = 60
const MAX_PULL = 100

export function PullToRefresh({ onRefresh, children, disabled }: PullToRefreshProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const startYRef = useRef(0)
  const pullingRef = useRef(false)
  const pullDistanceRef = useRef(0)
  const [pullDistance, setPullDistance] = useState(0)
  const [state, setState] = useState<'idle' | 'pulling' | 'refreshing'>('idle')

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    if (disabled || state === 'refreshing') return
    const container = containerRef.current
    if (!container) return
    // Only activate when scrolled to the very top
    if (container.scrollTop !== 0) return
    const touch = e.touches[0]
    if (!touch) return
    startYRef.current = touch.clientY
    pullingRef.current = true
  }, [disabled, state])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    if (!pullingRef.current || disabled || state === 'refreshing') return
    const container = containerRef.current
    if (!container) return
    // If scrolled down during pull, cancel
    if (container.scrollTop > 0) {
      pullingRef.current = false
      setPullDistance(0)
      pullDistanceRef.current = 0
      setState('idle')
      return
    }
    const touch = e.touches[0]
    if (!touch) return
    const dy = touch.clientY - startYRef.current
    if (dy > 0) {
      // Prevent Chrome Android native pull-to-refresh from competing
      e.preventDefault()
      // Apply resistance: the further you pull, the harder it gets
      const distance = Math.min(dy * 0.5, MAX_PULL)
      setPullDistance(distance)
      pullDistanceRef.current = distance
      setState('pulling')
    } else {
      setPullDistance(0)
      pullDistanceRef.current = 0
      setState('idle')
    }
  }, [disabled, state])

  const handleTouchEnd = useCallback(async () => {
    if (!pullingRef.current) return
    pullingRef.current = false

    // Use ref to avoid stale closure — state `pullDistance` may not reflect latest touchmove
    if (state !== 'pulling' || pullDistanceRef.current < THRESHOLD || disabled) {
      setPullDistance(0)
      pullDistanceRef.current = 0
      setState('idle')
      return
    }

    setState('refreshing')
    setPullDistance(THRESHOLD)
    try {
      await onRefresh()
    } finally {
      setPullDistance(0)
      pullDistanceRef.current = 0
      setState('idle')
    }
  }, [state, disabled, onRefresh])

  return (
    <div
      ref={containerRef}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
      className="relative overflow-auto"
      style={{ touchAction: state === 'pulling' ? 'none' : 'auto' }}
    >
      {/* Pull indicator */}
      <div
        className="flex items-center justify-center overflow-hidden transition-[height] duration-200 ease-out"
        style={{
          height: pullDistance > 0 ? `${pullDistance}px` : '0px',
          ...(state !== 'pulling' ? { transitionDuration: '300ms' } : { transitionDuration: '0ms' }),
        }}
      >
        <Loader2
          className={`h-5 w-5 text-muted-foreground ${state === 'refreshing' ? 'animate-spin' : ''}`}
          style={{
            transform: state === 'pulling' ? `rotate(${(pullDistance / THRESHOLD) * 360}deg)` : undefined,
            opacity: Math.min(pullDistance / THRESHOLD, 1),
          }}
        />
      </div>

      {children}
    </div>
  )
}
