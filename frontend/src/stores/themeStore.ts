import { create } from 'zustand'
import type { Theme } from '@/types'
import {
  getStoredTheme,
  setStoredTheme,
  applyTheme,
  subscribeToSystemThemeChanges,
  getResolvedTheme,
} from '@/lib/theme'

interface ThemeState {
  theme: Theme
  resolvedTheme: 'light' | 'dark'
}

interface ThemeActions {
  setTheme: (theme: Theme) => void
  initTheme: () => void
}

type ThemeStore = ThemeState & ThemeActions

export const useThemeStore = create<ThemeStore>((set, get) => ({
  // State
  theme: 'system',
  resolvedTheme: 'light',

  // Actions
  setTheme: (theme: Theme) => {
    setStoredTheme(theme)
    applyTheme(theme)
    set({
      theme,
      resolvedTheme: getResolvedTheme(theme),
    })
  },

  initTheme: () => {
    const storedTheme = getStoredTheme()
    applyTheme(storedTheme)

    set({
      theme: storedTheme,
      resolvedTheme: getResolvedTheme(storedTheme),
    })

    // Subscribe to system theme changes
    subscribeToSystemThemeChanges((systemTheme) => {
      const currentTheme = get().theme
      if (currentTheme === 'system') {
        applyTheme('system')
        set({ resolvedTheme: systemTheme })
      }
    })
  },
}))
