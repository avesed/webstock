import { useState, useEffect, useCallback } from 'react'

export type MarketId = 'us' | 'hk' | 'cn' | 'metal'
export type MarketStatusType = 'open' | 'closed' | 'preMarket' | 'afterHours'

export interface MarketSession {
  id: MarketId
  status: MarketStatusType
  nextEvent?: string
}

/**
 * Format a duration in milliseconds to a human-readable string.
 * Examples: "2h 15m", "45m", "23h 59m"
 */
function formatDuration(ms: number): string {
  if (ms <= 0) return '0m'

  const totalMinutes = Math.floor(ms / 60_000)
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60

  if (hours > 0 && minutes > 0) return `${hours}h ${minutes}m`
  if (hours > 0) return `${hours}h`
  return `${minutes}m`
}

/**
 * Get the UTC day of week (0 = Sunday, 6 = Saturday) and
 * time-of-day in minutes since midnight UTC.
 */
function getUtcDayAndMinutes(now: Date): { day: number; minutes: number } {
  const day = now.getUTCDay()
  const minutes = now.getUTCHours() * 60 + now.getUTCMinutes()
  return { day, minutes }
}

/**
 * Compute milliseconds until a target UTC day+minute, from the current time.
 * Handles wrap-around across week boundaries.
 */
function msUntil(now: Date, targetDay: number, targetMinute: number): number {
  const target = new Date(now)
  target.setUTCHours(Math.floor(targetMinute / 60), targetMinute % 60, 0, 0)

  const currentDay = now.getUTCDay()
  let dayDiff = targetDay - currentDay
  if (dayDiff < 0) dayDiff += 7
  if (dayDiff === 0 && target.getTime() <= now.getTime()) {
    dayDiff = 7
  }

  target.setUTCDate(target.getUTCDate() + dayDiff)
  return target.getTime() - now.getTime()
}

// Trading hours in UTC minutes (hour * 60 + minute)
const US_PRE_MARKET_OPEN = 9 * 60       // 09:00 UTC
const US_MARKET_OPEN = 13 * 60 + 30     // 13:30 UTC
const US_MARKET_CLOSE = 20 * 60         // 20:00 UTC
const US_AFTER_HOURS_CLOSE = 25 * 60    // 01:00 UTC next day (25:00 for easier math)

const HK_MARKET_OPEN = 1 * 60 + 30     // 01:30 UTC
const HK_MARKET_CLOSE = 8 * 60         // 08:00 UTC

const CN_MARKET_OPEN = 1 * 60 + 30     // 01:30 UTC
const CN_MARKET_CLOSE = 7 * 60         // 07:00 UTC

const METAL_CLOSE_FRIDAY = 22 * 60     // Fri 22:00 UTC
const METAL_OPEN_SUNDAY = 23 * 60      // Sun 23:00 UTC

function isWeekday(day: number): boolean {
  return day >= 1 && day <= 5
}

function computeUsStatus(now: Date): MarketSession {
  const { day, minutes } = getUtcDayAndMinutes(now)

  // After-hours spans midnight: 20:00-01:00 next day
  // Handle the 00:00-01:00 window (still after-hours from previous trading day)
  if (minutes < 60) {
    // 00:00-01:00 UTC range - this is after-hours if the *previous* day was a weekday
    const prevDay = day === 0 ? 6 : day - 1
    if (isWeekday(prevDay)) {
      const msRemaining = (60 - minutes) * 60_000
      return { id: 'us', status: 'afterHours', nextEvent: formatDuration(msRemaining) }
    }
  }

  if (!isWeekday(day)) {
    // Weekend - find next Monday pre-market
    const nextMonday = day === 0 ? 1 : 8 - day
    const ms = msUntil(now, (day + nextMonday) % 7, US_PRE_MARKET_OPEN)
    return { id: 'us', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < US_PRE_MARKET_OPEN) {
    const ms = (US_PRE_MARKET_OPEN - minutes) * 60_000
    return { id: 'us', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < US_MARKET_OPEN) {
    const ms = (US_MARKET_OPEN - minutes) * 60_000
    return { id: 'us', status: 'preMarket', nextEvent: formatDuration(ms) }
  }

  if (minutes < US_MARKET_CLOSE) {
    const ms = (US_MARKET_CLOSE - minutes) * 60_000
    return { id: 'us', status: 'open', nextEvent: formatDuration(ms) }
  }

  // After-hours: 20:00-01:00 (next day)
  // minutes >= US_MARKET_CLOSE, still same day
  const msRemaining = (US_AFTER_HOURS_CLOSE - minutes) * 60_000
  return { id: 'us', status: 'afterHours', nextEvent: formatDuration(msRemaining) }
}

function computeHkStatus(now: Date): MarketSession {
  const { day, minutes } = getUtcDayAndMinutes(now)

  if (!isWeekday(day)) {
    const nextMonday = day === 0 ? 1 : 8 - day
    const ms = msUntil(now, (day + nextMonday) % 7, HK_MARKET_OPEN)
    return { id: 'hk', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < HK_MARKET_OPEN) {
    const ms = (HK_MARKET_OPEN - minutes) * 60_000
    return { id: 'hk', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < HK_MARKET_CLOSE) {
    const ms = (HK_MARKET_CLOSE - minutes) * 60_000
    return { id: 'hk', status: 'open', nextEvent: formatDuration(ms) }
  }

  // Closed for the day - next open is tomorrow (or Monday if Friday)
  if (day === 5) {
    // Friday after close - next open is Monday
    const ms = msUntil(now, 1, HK_MARKET_OPEN)
    return { id: 'hk', status: 'closed', nextEvent: formatDuration(ms) }
  }

  // Next day open
  const ms = msUntil(now, (day + 1) % 7, HK_MARKET_OPEN)
  return { id: 'hk', status: 'closed', nextEvent: formatDuration(ms) }
}

function computeCnStatus(now: Date): MarketSession {
  const { day, minutes } = getUtcDayAndMinutes(now)

  if (!isWeekday(day)) {
    const nextMonday = day === 0 ? 1 : 8 - day
    const ms = msUntil(now, (day + nextMonday) % 7, CN_MARKET_OPEN)
    return { id: 'cn', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < CN_MARKET_OPEN) {
    const ms = (CN_MARKET_OPEN - minutes) * 60_000
    return { id: 'cn', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (minutes < CN_MARKET_CLOSE) {
    const ms = (CN_MARKET_CLOSE - minutes) * 60_000
    return { id: 'cn', status: 'open', nextEvent: formatDuration(ms) }
  }

  // Closed for the day
  if (day === 5) {
    const ms = msUntil(now, 1, CN_MARKET_OPEN)
    return { id: 'cn', status: 'closed', nextEvent: formatDuration(ms) }
  }

  const ms = msUntil(now, (day + 1) % 7, CN_MARKET_OPEN)
  return { id: 'cn', status: 'closed', nextEvent: formatDuration(ms) }
}

function computeMetalStatus(now: Date): MarketSession {
  const { day, minutes } = getUtcDayAndMinutes(now)

  // Metal market: Sun 23:00 UTC - Fri 22:00 UTC (nearly 24/5)
  // Closed window: Fri 22:00 UTC -> Sun 23:00 UTC

  if (day === 5 && minutes >= METAL_CLOSE_FRIDAY) {
    // Friday after 22:00 - closed until Sunday 23:00
    const ms = msUntil(now, 0, METAL_OPEN_SUNDAY)
    return { id: 'metal', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (day === 6) {
    // All Saturday - closed until Sunday 23:00
    const ms = msUntil(now, 0, METAL_OPEN_SUNDAY)
    return { id: 'metal', status: 'closed', nextEvent: formatDuration(ms) }
  }

  if (day === 0 && minutes < METAL_OPEN_SUNDAY) {
    // Sunday before 23:00 - still closed
    const ms = (METAL_OPEN_SUNDAY - minutes) * 60_000
    return { id: 'metal', status: 'closed', nextEvent: formatDuration(ms) }
  }

  // Market is open - compute time until Friday 22:00 close
  let daysUntilFriday = 5 - day
  if (daysUntilFriday < 0) daysUntilFriday += 7
  if (daysUntilFriday === 0 && minutes < METAL_CLOSE_FRIDAY) {
    // It's Friday but before close
    const ms = (METAL_CLOSE_FRIDAY - minutes) * 60_000
    return { id: 'metal', status: 'open', nextEvent: formatDuration(ms) }
  }

  const ms = msUntil(now, 5, METAL_CLOSE_FRIDAY)
  return { id: 'metal', status: 'open', nextEvent: formatDuration(ms) }
}

function computeAllMarkets(now: Date): MarketSession[] {
  return [
    computeUsStatus(now),
    computeHkStatus(now),
    computeCnStatus(now),
    computeMetalStatus(now),
  ]
}

const REFRESH_INTERVAL_MS = 60_000

/**
 * Pure client-side hook that computes market open/closed status for
 * US, HK, CN, and Metal markets based on UTC trading hours.
 *
 * Re-evaluates every 60 seconds and cleans up on unmount.
 */
export function useMarketStatus(): MarketSession[] {
  const [sessions, setSessions] = useState<MarketSession[]>(() =>
    computeAllMarkets(new Date())
  )

  const refresh = useCallback(() => {
    setSessions(computeAllMarkets(new Date()))
  }, [])

  useEffect(() => {
    // Refresh immediately in case the component mounted with stale initial state
    refresh()

    const intervalId = setInterval(refresh, REFRESH_INTERVAL_MS)
    return () => clearInterval(intervalId)
  }, [refresh])

  return sessions
}
