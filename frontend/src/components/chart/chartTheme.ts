import { ColorType } from 'lightweight-charts'

// ─── Shared chart theme constants for lightweight-charts ───

export const lightTheme = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#374151',
  },
  grid: {
    vertLines: { color: '#e5e7eb' },
    horzLines: { color: '#e5e7eb' },
  },
  crosshair: {
    vertLine: {
      color: '#6b7280',
      labelBackgroundColor: '#374151',
    },
    horzLine: {
      color: '#6b7280',
      labelBackgroundColor: '#374151',
    },
  },
  rightPriceScale: {
    borderColor: '#e5e7eb',
  },
  timeScale: {
    borderColor: '#e5e7eb',
  },
}

export const darkTheme = {
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#d1d5db',
  },
  grid: {
    vertLines: { color: '#374151' },
    horzLines: { color: '#374151' },
  },
  crosshair: {
    vertLine: {
      color: '#9ca3af',
      labelBackgroundColor: '#1f2937',
    },
    horzLine: {
      color: '#9ca3af',
      labelBackgroundColor: '#1f2937',
    },
  },
  rightPriceScale: {
    borderColor: '#374151',
  },
  timeScale: {
    borderColor: '#374151',
  },
}

// ─── Theme-aware chart colors ───
// Avoids pure black (#000) or pure white (#fff).
// Light theme uses deeper shades (-600) for contrast on white backgrounds.
// Dark theme uses brighter shades (-400) for visibility on dark backgrounds.

export function getChartColors(theme: 'light' | 'dark') {
  const isLight = theme === 'light'
  return {
    // Candlestick / area chart direction colors
    up: isLight ? '#16a34a' : '#4ade80',           // green-600 / green-400
    down: isLight ? '#dc2626' : '#f87171',          // red-600 / red-400
    upFill: isLight ? 'rgba(22, 163, 74, 0.5)' : 'rgba(74, 222, 128, 0.65)',
    downFill: isLight ? 'rgba(220, 38, 38, 0.5)' : 'rgba(248, 113, 113, 0.65)',

    // Area chart gradient fills
    areaUpTop: isLight ? 'rgba(22, 163, 74, 0.20)' : 'rgba(74, 222, 128, 0.25)',
    areaUpBottom: 'rgba(22, 163, 74, 0.02)',
    areaDownTop: isLight ? 'rgba(220, 38, 38, 0.20)' : 'rgba(248, 113, 113, 0.25)',
    areaDownBottom: 'rgba(220, 38, 38, 0.02)',

    // MA line colors — dark mode uses brighter/lighter variants for visibility
    maColors: isLight
      ? ['#2962FF', '#FF6D00', '#AA00FF', '#00BFA5', '#F59E0B']
      : ['#64B5F6', '#FFAB40', '#CE93D8', '#4DB6AC', '#FFD600'],

    // Bollinger Bands — higher opacity in dark mode for visibility
    bbColor: isLight ? 'rgba(103, 58, 183, 0.8)' : 'rgba(179, 157, 219, 0.9)',
    bbBandColor: isLight ? 'rgba(103, 58, 183, 0.5)' : 'rgba(179, 157, 219, 0.7)',

    // RSI — fuchsia-600 (light) / fuchsia-400 (dark)
    rsiColor: isLight ? '#C026D3' : '#E879F9',

    // MACD — brighter in dark mode
    macdLineColor: isLight ? '#2196F3' : '#64B5F6',
    macdSignalColor: isLight ? '#FF9800' : '#FFAB40',

    // Sentiment baseline
    sentimentUpLine: 'rgba(34, 197, 94, 1)',
    sentimentUpFill1: 'rgba(34, 197, 94, 0.28)',
    sentimentUpFill2: 'rgba(34, 197, 94, 0.05)',
    sentimentDownLine: 'rgba(239, 68, 68, 1)',
    sentimentDownFill1: 'rgba(239, 68, 68, 0.05)',
    sentimentDownFill2: 'rgba(239, 68, 68, 0.28)',

    // RSI reference lines — higher opacity in dark mode
    rsiOverbought: isLight ? 'rgba(239, 68, 68, 0.4)' : 'rgba(248, 113, 113, 0.6)',
    rsiOversold: isLight ? 'rgba(34, 197, 94, 0.4)' : 'rgba(74, 222, 128, 0.6)',
  }
}
