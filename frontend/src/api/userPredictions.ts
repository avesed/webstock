/**
 * User-facing prediction API (read-only).
 *
 * Calls `/predictions/...` endpoints (NOT `/admin/predictions/...`).
 * Reuses types and camelCase transforms from the admin predictions module.
 */
import apiClient from './client'
import type { PredictionResult, PerformanceResponse } from './predictions'
import { toCamelPrediction, toCamelPerformance } from './predictions'

// ── Types ──────────────────────────────────────

export interface PredictionSummary {
  market: string
  model: {
    modelDate: string
    qualityPassed: boolean
    featureCount: number
    symbolCount: number
  } | null
  accuracy: {
    hitRate: number
    avgIc: number
    totalPredictions: number
    days: number
  } | null
  predictionDate: string | null
}

export interface LatestPredictionsResponse {
  market: string
  predictionDate: string | null
  predictions: PredictionResult[]
  totalCount: number
}

export interface SymbolPredictionResponse {
  market: string
  prediction: PredictionResult
}

// ── snake_case → camelCase transforms ─────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelSummary(raw: Record<string, any>): PredictionSummary {
  const model = raw.model as Record<string, any> | null
  const accuracy = raw.accuracy as Record<string, any> | null
  return {
    market: raw.market as string,
    model: model
      ? {
          modelDate: (model.model_date ?? model.modelDate) as string,
          qualityPassed: (model.quality_passed ?? model.qualityPassed ?? false) as boolean,
          featureCount: (model.feature_count ?? model.featureCount) as number,
          symbolCount: (model.symbol_count ?? model.symbolCount) as number,
        }
      : null,
    accuracy: accuracy
      ? {
          hitRate: (accuracy.hit_rate ?? accuracy.hitRate) as number,
          avgIc: (accuracy.avg_ic ?? accuracy.avgIc) as number,
          totalPredictions: (accuracy.total_predictions ?? accuracy.totalPredictions) as number,
          days: (accuracy.days ?? 30) as number,
        }
      : null,
    predictionDate: (raw.prediction_date ?? raw.predictionDate ?? null) as string | null,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelLatest(raw: Record<string, any>): LatestPredictionsResponse {
  const predictions = Array.isArray(raw.predictions)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ? (raw.predictions as Record<string, any>[]).map(toCamelPrediction)
    : []
  return {
    market: raw.market as string,
    predictionDate: (raw.prediction_date ?? raw.predictionDate ?? null) as string | null,
    predictions,
    totalCount: (raw.total_count ?? raw.totalCount ?? predictions.length) as number,
  }
}

// ── API functions ──────────────────────────────

/** Latest predictions for a market (top + bottom ranked stocks). */
export async function getLatestPredictions(
  market: string,
  topN = 50
): Promise<LatestPredictionsResponse> {
  const { data } = await apiClient.get(`/predictions/${market}/latest`, {
    params: { top_n: topN },
  })
  return toCamelLatest(data)
}

/** Combined model + accuracy summary for a market. */
export async function getPredictionSummary(
  market: string
): Promise<PredictionSummary> {
  const { data } = await apiClient.get(`/predictions/${market}/summary`)
  return toCamelSummary(data)
}

/** IC and hit rate trends over time. */
export async function getPerformanceTrends(
  market: string,
  days = 60
): Promise<PerformanceResponse> {
  const { data } = await apiClient.get(`/predictions/${market}/performance`, {
    params: { days },
  })
  return toCamelPerformance(data)
}

/** Prediction for a single stock symbol. */
export async function getSymbolPrediction(
  symbol: string
): Promise<SymbolPredictionResponse> {
  const { data } = await apiClient.get(`/predictions/symbol/${symbol}`)
  const pred = toCamelPrediction(
    (data.prediction ?? {}) as Record<string, unknown>
  )
  return { market: data.market as string, prediction: pred }
}
