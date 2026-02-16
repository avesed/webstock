import { useState, useEffect } from 'react'

const MOBILE_BREAKPOINT = 1023 // Matches Tailwind lg: breakpoint (1024px)

export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(() => {
    if (typeof window === 'undefined') return false
    return window.innerWidth <= MOBILE_BREAKPOINT
  })

  useEffect(() => {
    const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`)
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches)

    // Sync initial state
    setIsMobile(mql.matches)

    mql.addEventListener('change', handler)
    return () => mql.removeEventListener('change', handler)
  }, [])

  return isMobile
}
