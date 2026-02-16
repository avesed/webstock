import type { Theme } from '@/types'

const THEME_STORAGE_KEY = 'webstock-theme'

/**
 * Get the stored theme preference
 */
export function getStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'system'

  const stored = localStorage.getItem(THEME_STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored
  }
  return 'system'
}

/**
 * Store the theme preference
 */
export function setStoredTheme(theme: Theme): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(THEME_STORAGE_KEY, theme)
}

/**
 * Get the system's preferred color scheme
 */
export function getSystemTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/**
 * Get the resolved theme (accounting for 'system' preference)
 */
export function getResolvedTheme(theme: Theme): 'light' | 'dark' {
  if (theme === 'system') {
    return getSystemTheme()
  }
  return theme
}

/**
 * Apply theme to the document
 */
export function applyTheme(theme: Theme): void {
  if (typeof window === 'undefined') return

  const root = window.document.documentElement
  root.classList.remove('light', 'dark')

  const resolvedTheme = getResolvedTheme(theme)
  root.classList.add(resolvedTheme)
}

/**
 * Subscribe to system theme changes
 */
export function subscribeToSystemThemeChanges(callback: (theme: 'light' | 'dark') => void): () => void {
  if (typeof window === 'undefined') return () => {}

  const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')

  const handler = (e: MediaQueryListEvent) => {
    callback(e.matches ? 'dark' : 'light')
  }

  mediaQuery.addEventListener('change', handler)
  return () => mediaQuery.removeEventListener('change', handler)
}

/**
 * Get chart theme based on current theme
 */
export function getChartTheme(theme: Theme): {
  background: string
  textColor: string
  gridColor: string
  upColor: string
  downColor: string
  borderUpColor: string
  borderDownColor: string
  wickUpColor: string
  wickDownColor: string
} {
  const resolvedTheme = getResolvedTheme(theme)

  if (resolvedTheme === 'dark') {
    return {
      background: 'hsl(222.2, 84%, 4.9%)',
      textColor: 'hsl(210, 40%, 98%)',
      gridColor: 'hsl(217.2, 32.6%, 17.5%)',
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderUpColor: '#16a34a',
      borderDownColor: '#dc2626',
      wickUpColor: '#22c55e',
      wickDownColor: '#ef4444',
    }
  }

  return {
    background: 'hsl(0, 0%, 100%)',
    textColor: 'hsl(222.2, 84%, 4.9%)',
    gridColor: 'hsl(214.3, 31.8%, 91.4%)',
    upColor: '#16a34a',
    downColor: '#dc2626',
    borderUpColor: '#15803d',
    borderDownColor: '#b91c1c',
    wickUpColor: '#16a34a',
    wickDownColor: '#dc2626',
  }
}
