import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

// Import all translation files statically
import enCommon from './locales/en/common.json'
import enAuth from './locales/en/auth.json'
import enDashboard from './locales/en/dashboard.json'
import enChat from './locales/en/chat.json'
import enSettings from './locales/en/settings.json'

import zhCommon from './locales/zh/common.json'
import zhAuth from './locales/zh/auth.json'
import zhDashboard from './locales/zh/dashboard.json'
import zhChat from './locales/zh/chat.json'
import zhSettings from './locales/zh/settings.json'

export const defaultNS = 'common'
export const supportedLngs = ['en', 'zh'] as const
export type SupportedLanguage = (typeof supportedLngs)[number]

// Exported for use in useLocale hook - single source of truth
export const LOCALE_STORAGE_KEY = 'webstock-locale'

export const resources = {
  en: {
    common: enCommon,
    auth: enAuth,
    dashboard: enDashboard,
    chat: enChat,
    settings: enSettings,
  },
  zh: {
    common: zhCommon,
    auth: zhAuth,
    dashboard: zhDashboard,
    chat: zhChat,
    settings: zhSettings,
  },
} as const

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs,
    defaultNS,
    ns: ['common', 'auth', 'dashboard', 'chat', 'settings'],

    detection: {
      order: ['localStorage', 'navigator'],
      lookupLocalStorage: LOCALE_STORAGE_KEY,
      caches: ['localStorage'],
    },

    interpolation: {
      escapeValue: false, // React already escapes values
    },

    react: {
      useSuspense: true,
    },

    // Log missing translation keys in development mode
    saveMissing: import.meta.env.DEV,
    missingKeyHandler: import.meta.env.DEV
      ? (
          _lngs: readonly string[],
          ns: string,
          key: string,
          fallbackValue: string
        ) => {
          console.warn(
            `[i18n] Missing translation key: "${key}" in namespace "${ns}". Fallback: "${fallbackValue}"`
          )
        }
      : false,
  })

export default i18n
