import { useMemo } from 'react'
import { useLocale } from './useLocale'

type CurrencyCode = 'USD' | 'CNY' | 'HKD' | 'EUR' | 'GBP' | 'JPY'

// Fallback value for invalid inputs
const INVALID_VALUE_FALLBACK = '\u2013' // en-dash

interface UseFormattersReturn {
  formatCurrency: (value: number, currency?: CurrencyCode) => string
  formatDate: (date: Date | string | number, options?: Intl.DateTimeFormatOptions) => string
  formatRelativeTime: (date: Date | string | number) => string
  formatNumber: (value: number, decimals?: number) => string
  formatCompactNumber: (value: number) => string
  formatPercent: (value: number, decimals?: number) => string
}

function getIntlLocale(locale: string): string {
  const localeMap: Record<string, string> = {
    zh: 'zh-CN',
    en: 'en-US',
  }
  return localeMap[locale] ?? 'en-US'
}

/**
 * Check if a number is valid for formatting (not NaN or Infinity)
 */
function isValidNumber(value: number): boolean {
  return typeof value === 'number' && Number.isFinite(value)
}

/**
 * Check if a date is valid
 */
function isValidDate(date: Date): boolean {
  return date instanceof Date && !isNaN(date.getTime())
}

/**
 * Safely parse a date value into a Date object
 * Returns null if the date is invalid
 */
function safeParseDate(date: Date | string | number): Date | null {
  const dateObj = date instanceof Date ? date : new Date(date)
  return isValidDate(dateObj) ? dateObj : null
}

export function useFormatters(): UseFormattersReturn {
  const { locale } = useLocale()
  const intlLocale = getIntlLocale(locale)

  return useMemo(() => {
    const formatCurrency = (value: number, currency: CurrencyCode = 'USD'): string => {
      if (!isValidNumber(value)) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        return new Intl.NumberFormat(intlLocale, {
          style: 'currency',
          currency,
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        }).format(value)
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    const formatDate = (
      date: Date | string | number,
      options?: Intl.DateTimeFormatOptions
    ): string => {
      const dateObj = safeParseDate(date)
      if (!dateObj) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        const defaultOptions: Intl.DateTimeFormatOptions = {
          year: 'numeric',
          month: 'short',
          day: 'numeric',
        }
        return new Intl.DateTimeFormat(intlLocale, options ?? defaultOptions).format(dateObj)
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    const formatRelativeTime = (date: Date | string | number): string => {
      const dateObj = safeParseDate(date)
      if (!dateObj) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        const now = new Date()
        const diffInSeconds = Math.floor((now.getTime() - dateObj.getTime()) / 1000)

        const rtf = new Intl.RelativeTimeFormat(intlLocale, { numeric: 'auto' })

        // Calculate the most appropriate unit
        const minute = 60
        const hour = minute * 60
        const day = hour * 24
        const week = day * 7
        const month = day * 30
        const year = day * 365

        if (diffInSeconds < minute) {
          return rtf.format(-diffInSeconds, 'second')
        } else if (diffInSeconds < hour) {
          return rtf.format(-Math.floor(diffInSeconds / minute), 'minute')
        } else if (diffInSeconds < day) {
          return rtf.format(-Math.floor(diffInSeconds / hour), 'hour')
        } else if (diffInSeconds < week) {
          return rtf.format(-Math.floor(diffInSeconds / day), 'day')
        } else if (diffInSeconds < month) {
          return rtf.format(-Math.floor(diffInSeconds / week), 'week')
        } else if (diffInSeconds < year) {
          return rtf.format(-Math.floor(diffInSeconds / month), 'month')
        } else {
          return rtf.format(-Math.floor(diffInSeconds / year), 'year')
        }
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    const formatNumber = (value: number, decimals = 2): string => {
      if (!isValidNumber(value)) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        return new Intl.NumberFormat(intlLocale, {
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        }).format(value)
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    const formatCompactNumber = (value: number): string => {
      if (!isValidNumber(value)) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        return new Intl.NumberFormat(intlLocale, {
          notation: 'compact',
          compactDisplay: 'short',
          maximumFractionDigits: 1,
        }).format(value)
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    const formatPercent = (value: number, decimals = 2): string => {
      if (!isValidNumber(value)) {
        return INVALID_VALUE_FALLBACK
      }
      try {
        return new Intl.NumberFormat(intlLocale, {
          style: 'percent',
          minimumFractionDigits: decimals,
          maximumFractionDigits: decimals,
        }).format(value / 100)
      } catch {
        return INVALID_VALUE_FALLBACK
      }
    }

    return {
      formatCurrency,
      formatDate,
      formatRelativeTime,
      formatNumber,
      formatCompactNumber,
      formatPercent,
    }
  }, [intlLocale])
}
