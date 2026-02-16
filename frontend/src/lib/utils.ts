import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { WeightUnit } from '@/types'

/**
 * Merge Tailwind CSS classes with clsx
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Format a number as currency
 */
export function formatCurrency(
  value: number,
  currency: string = 'USD',
  locale: string = 'en-US'
): string {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

/**
 * Format a number with compact notation (e.g., 1.5M, 2.3B)
 */
export function formatCompactNumber(value: number, locale: string = 'en-US'): string {
  return new Intl.NumberFormat(locale, {
    notation: 'compact',
    compactDisplay: 'short',
    maximumFractionDigits: 2,
  }).format(value)
}

/**
 * Format a percentage value
 */
export function formatPercent(value: number, decimals: number = 2): string {
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(decimals)}%`
}

/**
 * Format a number with thousand separators
 */
export function formatNumber(
  value: number,
  decimals: number = 2,
  locale: string = 'en-US'
): string {
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value)
}

/**
 * Format a date string
 */
export function formatDate(
  date: string | Date,
  options: Intl.DateTimeFormatOptions = {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  },
  locale: string = 'en-US'
): string {
  const d = typeof date === 'string' ? new Date(date) : date
  return new Intl.DateTimeFormat(locale, options).format(d)
}

/**
 * Format a date as relative time (e.g., "2 hours ago")
 */
export function formatRelativeTime(date: string | Date, locale: string = 'en-US'): string {
  const d = typeof date === 'string' ? new Date(date) : date
  const now = new Date()
  const diffInSeconds = Math.floor((now.getTime() - d.getTime()) / 1000)

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })

  if (diffInSeconds < 60) {
    return rtf.format(-diffInSeconds, 'second')
  } else if (diffInSeconds < 3600) {
    return rtf.format(-Math.floor(diffInSeconds / 60), 'minute')
  } else if (diffInSeconds < 86400) {
    return rtf.format(-Math.floor(diffInSeconds / 3600), 'hour')
  } else if (diffInSeconds < 2592000) {
    return rtf.format(-Math.floor(diffInSeconds / 86400), 'day')
  } else if (diffInSeconds < 31536000) {
    return rtf.format(-Math.floor(diffInSeconds / 2592000), 'month')
  } else {
    return rtf.format(-Math.floor(diffInSeconds / 31536000), 'year')
  }
}

const HTML_ENTITY_MAP: Record<string, string> = {
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&apos;': "'",
}

/**
 * Decode HTML entities (named, hex, and decimal) in a string
 */
export function decodeHtmlEntities(text: string): string {
  return text.replace(
    /&(?:#x([0-9a-fA-F]+)|#(\d+)|amp|lt|gt|quot|apos);/g,
    (match, hex, dec) => {
      if (hex != null) return String.fromCodePoint(parseInt(hex, 16))
      if (dec != null) return String.fromCodePoint(parseInt(dec, 10))
      return HTML_ENTITY_MAP[match] ?? match
    },
  )
}

/**
 * Get the color class based on price change
 */
export function getPriceChangeColor(change: number): string {
  if (change > 0) return 'text-stock-up'
  if (change < 0) return 'text-stock-down'
  return 'text-stock-neutral'
}

/**
 * Get the background color class based on price change
 */
export function getPriceChangeBgColor(change: number): string {
  if (change > 0) return 'bg-stock-up'
  if (change < 0) return 'bg-stock-down'
  return 'bg-muted'
}

/**
 * Debounce function
 */
export function debounce<T extends (...args: Parameters<T>) => ReturnType<T>>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: ReturnType<typeof setTimeout> | null = null

  return function (...args: Parameters<T>) {
    if (timeout) {
      clearTimeout(timeout)
    }
    timeout = setTimeout(() => {
      func(...args)
    }, wait)
  }
}

/**
 * Throttle function
 */
export function throttle<T extends (...args: Parameters<T>) => ReturnType<T>>(
  func: T,
  limit: number
): (...args: Parameters<T>) => void {
  let inThrottle = false

  return function (...args: Parameters<T>) {
    if (!inThrottle) {
      func(...args)
      inThrottle = true
      setTimeout(() => {
        inThrottle = false
      }, limit)
    }
  }
}

/**
 * Sleep for a given number of milliseconds
 */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Parse stock symbol to extract market information
 */
export function parseSymbol(symbol: string): { code: string; market: 'US' | 'HK' | 'CN' } {
  const upperSymbol = symbol.toUpperCase()

  if (upperSymbol.endsWith('.HK')) {
    return { code: upperSymbol, market: 'HK' }
  }

  if (upperSymbol.endsWith('.SS') || upperSymbol.endsWith('.SZ')) {
    return { code: upperSymbol, market: 'CN' }
  }

  // Default to US market
  return { code: upperSymbol, market: 'US' }
}

/**
 * Validate email format
 */
export function isValidEmail(email: string): boolean {
  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  return emailRegex.test(email)
}

/**
 * Validate password strength
 */
export function validatePassword(password: string): {
  isValid: boolean
  errors: string[]
} {
  const errors: string[] = []

  if (password.length < 8) {
    errors.push('Password must be at least 8 characters long')
  }

  if (!/[A-Z]/.test(password)) {
    errors.push('Password must contain at least one uppercase letter')
  }

  if (!/[a-z]/.test(password)) {
    errors.push('Password must contain at least one lowercase letter')
  }

  if (!/[0-9]/.test(password)) {
    errors.push('Password must contain at least one number')
  }

  return {
    isValid: errors.length === 0,
    errors,
  }
}

/**
 * Generate a unique ID
 */
export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

/**
 * Capitalize first letter of a string
 */
export function capitalize(str: string): string {
  return str.charAt(0).toUpperCase() + str.slice(1).toLowerCase()
}

/**
 * Truncate a string to a given length
 */
export function truncate(str: string, length: number): string {
  if (str.length <= length) return str
  return `${str.slice(0, length)}...`
}

/**
 * Check if symbol is a precious metal
 */
export function isMetal(symbol: string): boolean {
  return /^(GC|SI|PL|PA)=F$/i.test(symbol)
}

/**
 * Get asset type from symbol
 */
export function getAssetType(symbol: string): 'stock' | 'metal' {
  return isMetal(symbol) ? 'metal' : 'stock'
}

/**
 * Weight unit conversion factors (relative to troy oz)
 */
const WEIGHT_FACTORS: Record<WeightUnit, number> = {
  troy_oz: 1,
  gram: 31.1035,
  kilogram: 0.0311035,
}

/**
 * Convert weight between units
 */
export function convertWeight(value: number, from: WeightUnit, to: WeightUnit): number {
  if (from === to) return value
  // Convert to troy oz first
  const troyOz = value / WEIGHT_FACTORS[from]
  // Then to target unit
  return troyOz * WEIGHT_FACTORS[to]
}

/**
 * Convert price per troy oz to price per other unit
 */
export function convertPricePerUnit(pricePerTroyOz: number, toUnit: WeightUnit): number {
  if (toUnit === 'troy_oz') return pricePerTroyOz
  return pricePerTroyOz / WEIGHT_FACTORS[toUnit]
}
