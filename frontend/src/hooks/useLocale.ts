import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { LOCALE_STORAGE_KEY, type SupportedLanguage } from '@/i18n'

export type Locale = 'en' | 'zh'

/**
 * Safely set a value in localStorage.
 * Handles Safari private browsing and other environments where localStorage may throw.
 */
function safeLocalStorageSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch (error) {
    // localStorage may throw in Safari private browsing mode or when storage quota is exceeded
    console.warn(
      `[useLocale] Failed to persist locale to localStorage: ${error instanceof Error ? error.message : 'Unknown error'}`
    )
  }
}

interface UseLocaleReturn {
  locale: Locale
  setLocale: (locale: Locale) => void
}

export function useLocale(): UseLocaleReturn {
  const { i18n } = useTranslation()

  const locale = (i18n.language?.substring(0, 2) || 'en') as Locale

  const setLocale = useCallback(
    (newLocale: Locale) => {
      // Update i18next language
      i18n.changeLanguage(newLocale as SupportedLanguage)

      // Persist to localStorage (safe for Safari private browsing)
      safeLocalStorageSet(LOCALE_STORAGE_KEY, newLocale)

      // Update document lang attribute for accessibility and SEO
      document.documentElement.lang = newLocale
    },
    [i18n]
  )

  return {
    locale,
    setLocale,
  }
}
