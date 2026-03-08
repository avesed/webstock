import apiClient from './client'

// ── Types ──────────────────────────────────────

export interface PredictionResult {
  symbol: string
  predictedScore: number
  percentileRank: number
  predictedDirection: 'up' | 'down' | 'sideways'
  predictionDate?: string
  actualReturn?: number | null
}

export interface PredictionModel {
  id: string
  market: string
  modelDate: string
  featureCount: number
  symbolCount: number
  ic: number | null
  icir: number | null
  ndcg: number | null
  qualityPassed: boolean
  featureImportanceTop30: Record<string, number> | null
  ensembleSize?: number | undefined
  foldIcs?: number[] | undefined
  createdAt: string
}

export interface IcDecayResponse {
  market: string
  days: number
  horizons: Record<string, { avg_ic: number; ic_std: number; n_dates: number }>
  dataPoints: number
}

export interface TurnoverResponse {
  market: string
  days: number
  dataPoints: number
  summary: {
    avgRankAutocorr: number | null
    avgTopNRetention: number | null
    topN: number
    totalDates: number
  }
  daily: Array<{
    date: string
    rankAutocorr: number | null
    topNRetention: number | null
  }>
}

export interface SectorResponse {
  market: string
  totalSymbols: number
  uniqueSectors: number
  sectorCounts: Record<string, number>
}

export interface AttributionDaily {
  date: string
  portfolioReturn: number
  universeReturn: number
  sectorAttr: number
  sizeAttr: number
  alpha: number
}

export interface AttributionSummary {
  totalReturn: number
  sectorPct: number
  sizePct: number
  alphaPct: number
  avgDailyAlpha: number
  sectorBreakdown: Record<string, number>
}

export interface AttributionResponse {
  market: string
  days: number
  topN: number
  dataPoints: number
  daily: AttributionDaily[]
  summary: AttributionSummary
}

export interface PredictionDatesResponse {
  market: string
  forwardDays: number
  dates: string[]
  predictions: Record<string, PredictionResult[]>
}

export interface PredictionTask {
  taskId: string
  status: 'pending' | 'training' | 'predicting' | 'completed' | 'failed'
  progress: number | null
  message: string | null
}

export interface RDAgentStatus {
  market: string
  status: 'idle' | 'starting' | 'running' | 'completed' | 'failed' | 'stopped'
  currentRound: number
  maxRounds: number
  discoveredCount: number
  startedAt: string | null
  completedAt: string | null
  error: string | null
}

export interface DiscoveredFactor {
  id: string
  name: string
  expression: string
  description: string | null
  market: string
  ic: number | null
  icir: number | null
  discoveryRound: number | null
  isActive: boolean
  createdAt: string
}

export interface PredictionUniverse {
  id: string
  name: string
  market: string
  universeType: 'index' | 'custom'
  indexCode: string | null
  symbols: string[] | null
  isDefault: boolean
  isActive: boolean
}

export interface PredictionAccuracy {
  days: number
  market: string
  totalPredictions: number
  correctDirection: number
  accuracy: number
  avgIc: number | null
  avgIcir: number | null
  pendingCount: number
}

export interface FeatureImportance {
  modelId: string
  market: string
  modelDate: string | null
  featureCount: number
  top30: Record<string, number>
  full: Record<string, number> | null
}

export interface ModelPerformanceMetric {
  date: string
  ic: number | null
  hitRate: number | null
  top10Return: number
  bottom10Return: number
  spread: number
  symbolCount: number
}

export interface PerformanceResponse {
  market: string
  days: number
  dataPoints: number
  metrics: ModelPerformanceMetric[]
  summary: {
    avgIc: number | null
    avgHitRate: number | null
    avgSpread: number | null
    totalDates: number
    totalPredictions: number
  }
}

export interface PredictionStatusItem {
  models: PredictionModel[]
  latestPredictions: PredictionResult[]
  error?: string
}

// ── snake_case → camelCase transforms ─────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelPrediction(p: Record<string, any>): PredictionResult {
  const predictionDate = (p.prediction_date ?? p.predictionDate) as string | undefined
  const actualReturn = (p.actual_return ?? p.actualReturn) as number | null | undefined
  const result: PredictionResult = {
    symbol: p.symbol as string,
    predictedScore: (p.predicted_score ?? p.predictedScore) as number,
    percentileRank: (p.percentile_rank ?? p.percentileRank) as number,
    predictedDirection: (p.predicted_direction ?? p.predictedDirection) as PredictionResult['predictedDirection'],
  }
  if (predictionDate !== undefined) result.predictionDate = predictionDate
  if (actualReturn !== undefined) result.actualReturn = actualReturn
  return result
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelModel(m: Record<string, any>): PredictionModel {
  // Extract feature_importance_top30 from metadata if present
  const metadata = m.metadata as Record<string, any> | undefined
  const featureImportanceTop30 = (
    metadata?.feature_importance_top30 ??
    m.feature_importance_top30 ??
    m.featureImportanceTop30 ??
    null
  ) as Record<string, number> | null

  const ensembleSize = (metadata?.ensemble_size ?? m.ensemble_size ?? m.ensembleSize) as number | undefined
  const foldIcs = (metadata?.fold_ics ?? m.fold_ics ?? m.foldIcs) as number[] | undefined

  return {
    id: String(m.id),
    market: m.market as string,
    modelDate: (m.model_date ?? m.modelDate) as string,
    featureCount: (m.feature_count ?? m.featureCount) as number,
    symbolCount: (m.symbol_count ?? m.symbolCount) as number,
    ic: (m.ic ?? null) as number | null,
    icir: (m.icir ?? null) as number | null,
    ndcg: (m.ndcg ?? null) as number | null,
    qualityPassed: (m.quality_passed ?? m.qualityPassed ?? true) as boolean,
    featureImportanceTop30,
    ensembleSize,
    foldIcs,
    createdAt: (m.created_at ?? m.createdAt) as string,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelTask(t: Record<string, any>): PredictionTask {
  return {
    taskId: (t.task_id ?? t.taskId) as string,
    status: t.status as PredictionTask['status'],
    progress: (t.progress ?? null) as number | null,
    message: (t.message ?? null) as string | null,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelRDAgent(s: Record<string, any>): RDAgentStatus {
  return {
    market: s.market as string,
    status: s.status as RDAgentStatus['status'],
    currentRound: (s.current_round ?? s.currentRound ?? 0) as number,
    maxRounds: (s.max_rounds ?? s.maxRounds ?? 0) as number,
    discoveredCount: (s.discovered_count ?? s.discoveredCount ?? 0) as number,
    startedAt: (s.started_at ?? s.startedAt ?? null) as string | null,
    completedAt: (s.completed_at ?? s.completedAt ?? null) as string | null,
    error: (s.error ?? null) as string | null,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelFactor(f: Record<string, any>): DiscoveredFactor {
  return {
    id: String(f.id),
    name: f.name as string,
    expression: f.expression as string,
    description: (f.description ?? null) as string | null,
    market: f.market as string,
    ic: (f.ic ?? null) as number | null,
    icir: (f.icir ?? null) as number | null,
    discoveryRound: (f.discovery_round ?? f.discoveryRound ?? null) as number | null,
    isActive: (f.is_active ?? f.isActive) as boolean,
    createdAt: (f.created_at ?? f.createdAt) as string,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelAccuracy(a: Record<string, any>): PredictionAccuracy {
  return {
    days: (a.days) as number,
    market: (a.market) as string,
    totalPredictions: (a.total_predictions ?? a.totalPredictions) as number,
    correctDirection: (a.correct_direction ?? a.correctDirection) as number,
    accuracy: (a.accuracy) as number,
    avgIc: (a.avg_ic ?? a.avgIc ?? null) as number | null,
    avgIcir: (a.avg_icir ?? a.avgIcir ?? null) as number | null,
    pendingCount: (a.pending_count ?? a.pendingCount ?? 0) as number,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelPerformance(raw: Record<string, any>): PerformanceResponse {
  const summary = raw.summary as Record<string, any> | undefined
  return {
    market: raw.market as string,
    days: raw.days as number,
    dataPoints: (raw.data_points ?? raw.dataPoints ?? 0) as number,
    metrics: Array.isArray(raw.metrics) ? (raw.metrics as Record<string, any>[]).map(m => ({
      date: m.date as string,
      ic: (m.ic ?? null) as number | null,
      hitRate: (m.hit_rate ?? m.hitRate ?? null) as number | null,
      top10Return: (m.top10_return ?? m.top10Return ?? 0) as number,
      bottom10Return: (m.bottom10_return ?? m.bottom10Return ?? 0) as number,
      spread: (m.spread ?? 0) as number,
      symbolCount: (m.symbol_count ?? m.symbolCount ?? 0) as number,
    })) : [],
    summary: {
      avgIc: (summary?.avg_ic ?? summary?.avgIc ?? null) as number | null,
      avgHitRate: (summary?.avg_hit_rate ?? summary?.avgHitRate ?? null) as number | null,
      avgSpread: (summary?.avg_spread ?? summary?.avgSpread ?? null) as number | null,
      totalDates: (summary?.total_dates ?? summary?.totalDates ?? 0) as number,
      totalPredictions: (summary?.total_predictions ?? summary?.totalPredictions ?? 0) as number,
    },
  }
}

// ── API functions ──────────────────────────────

export const predictionsApi = {
  // Status — backend returns { status: string, markets: Record<string, ...> }
  getStatus: () =>
    apiClient.get<{ status: string; markets: Record<string, unknown> }>('/admin/predictions/status')
      .then(r => {
        const markets = r.data.markets
        const result: Record<string, PredictionStatusItem> = {}
        for (const [key, value] of Object.entries(markets)) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const v = value as Record<string, any>
          if (v.error) {
            result[key] = { models: [], latestPredictions: [], error: v.error as string }
          } else {
            // models may be { models: [...] } or [...]
            const rawModels = v.models as Record<string, unknown> | Record<string, unknown>[]
            const modelsList = Array.isArray(rawModels)
              ? rawModels
              : Array.isArray((rawModels as Record<string, unknown>)?.models)
                ? (rawModels as Record<string, unknown>).models as Record<string, unknown>[]
                : []
            // latestPredictions may be { predictions: [...] } or [...]
            const rawLP = (v.latestPredictions ?? v.latest_predictions) as Record<string, unknown> | Record<string, unknown>[]
            const lpList = Array.isArray(rawLP)
              ? rawLP
              : Array.isArray((rawLP as Record<string, unknown>)?.predictions)
                ? (rawLP as Record<string, unknown>).predictions as Record<string, unknown>[]
                : []
            result[key] = {
              models: modelsList.map(toCamelModel),
              latestPredictions: lpList.map(toCamelPrediction),
            }
          }
        }
        return result
      }),

  // Predictions
  triggerPrediction: (market: string, forceRetrain = false, forwardDays = 5) =>
    apiClient.post<Record<string, unknown>>(`/admin/predictions/${market}/trigger`, {
      force_retrain: forceRetrain,
      forward_days: forwardDays,
    }).then(r => toCamelTask(r.data)),

  getLatestPredictions: (market: string, topN = 50) =>
    apiClient.get<{ market: string; count: number; predictions: Record<string, unknown>[] }>(`/admin/predictions/${market}/latest`, {
      params: { top_n: topN },
    }).then(r => (r.data.predictions ?? []).map(toCamelPrediction)),

  getTaskStatus: (taskId: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/tasks/${taskId}`)
      .then(r => toCamelTask(r.data)),

  getModels: (market?: string) =>
    apiClient.get<{ models: Record<string, unknown>[] }>('/admin/predictions/models', {
      params: market ? { market } : {},
    }).then(r => (r.data.models ?? []).map(toCamelModel)),

  getAccuracy: (market: string, days = 30) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/accuracy`, {
      params: { days },
    }).then(r => toCamelAccuracy(r.data)),

  // RD-Agent
  startRDAgent: (market: string, maxRounds = 30, universeId?: string) =>
    apiClient.post<Record<string, unknown>>(`/admin/predictions/rdagent/${market}/start`, {
      max_rounds: maxRounds,
      universe_id: universeId,
    }).then(r => r.data),

  getRDAgentStatus: (market: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/rdagent/${market}/status`)
      .then(r => toCamelRDAgent(r.data)),

  stopRDAgent: (market: string) =>
    apiClient.post<{ message: string }>(`/admin/predictions/rdagent/${market}/stop`).then(r => r.data),

  // Factors — backend returns { count: N, factors: [...] }
  getFactors: (market?: string) =>
    apiClient.get<{ count: number; factors: Record<string, unknown>[] }>('/admin/predictions/factors', {
      params: market ? { market } : {},
    }).then(r => r.data.factors.map(toCamelFactor)),

  toggleFactor: (factorId: string, isActive: boolean) =>
    apiClient.put<Record<string, unknown>>(`/admin/predictions/factors/${factorId}`, {
      is_active: isActive,
    }).then(r => r.data),

  // Universes — backend returns { universes: [...] } with camelCase keys
  getUniverses: () =>
    apiClient.get<{ universes: PredictionUniverse[] }>('/admin/predictions/universes')
      .then(r => r.data.universes),

  createUniverse: (data: Partial<PredictionUniverse>) =>
    apiClient.post<PredictionUniverse>('/admin/predictions/universes', {
      name: data.name,
      market: data.market,
      universe_type: data.universeType,
      index_code: data.indexCode,
      symbols: data.symbols,
      is_default: data.isDefault,
    }).then(r => r.data),

  updateUniverse: (id: string, data: Partial<PredictionUniverse>) =>
    apiClient.put<PredictionUniverse>(`/admin/predictions/universes/${id}`, {
      name: data.name,
      market: data.market,
      universe_type: data.universeType,
      index_code: data.indexCode,
      symbols: data.symbols,
      is_default: data.isDefault,
      is_active: data.isActive,
    }).then(r => r.data),

  deleteUniverse: (id: string) =>
    apiClient.delete<{ message: string }>(`/admin/predictions/universes/${id}`).then(r => r.data),

  // Feature Importance
  getFeatureImportance: (modelId: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/models/${modelId}/feature-importance`)
      .then(r => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const d = r.data as Record<string, any>
        return {
          modelId: (d.model_id ?? d.modelId) as string,
          market: d.market as string,
          modelDate: (d.model_date ?? d.modelDate ?? null) as string | null,
          featureCount: (d.feature_count ?? d.featureCount ?? 0) as number,
          top30: (d.top30 ?? {}) as Record<string, number>,
          full: (d.full ?? null) as Record<string, number> | null,
        } satisfies FeatureImportance
      }),

  // Model Quality
  updateModelQuality: (modelId: string, qualityPassed: boolean) =>
    apiClient.put<Record<string, unknown>>(`/admin/predictions/models/${modelId}/quality`, {
      quality_passed: qualityPassed,
    }).then(r => r.data),

  // Performance Metrics
  getPerformanceMetrics: (market: string, days = 90) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/performance`, {
      params: { days },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }).then(r => toCamelPerformance(r.data as Record<string, any>)),

  // Fundamentals
  getFundamentalsStatus: () =>
    apiClient.get<{ lastUpdated: string | null; totalSymbols: number }>(
      '/admin/predictions/fundamentals/status'
    ).then(r => r.data),

  // Signal Quality — IC decay, turnover, sectors
  getIcDecay: (market: string, days = 60) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/ic-decay`, {
      params: { days },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }).then(r => {
      const d = r.data as Record<string, any>
      return {
        market: d.market as string,
        days: d.days as number,
        horizons: (d.horizons ?? {}) as IcDecayResponse['horizons'],
        dataPoints: (d.data_points ?? d.dataPoints ?? 0) as number,
      } satisfies IcDecayResponse
    }),

  getTurnover: (market: string, days = 60, topN = 20) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/turnover`, {
      params: { days, top_n: topN },
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    }).then(r => {
      const d = r.data as Record<string, any>
      const s = d.summary as Record<string, any> | undefined
      return {
        market: d.market as string,
        days: d.days as number,
        dataPoints: (d.data_points ?? d.dataPoints ?? 0) as number,
        summary: {
          avgRankAutocorr: (s?.avg_rank_autocorr ?? s?.avgRankAutocorr ?? null) as number | null,
          avgTopNRetention: (s?.avg_topN_retention ?? s?.avgTopNRetention ?? null) as number | null,
          topN: (s?.top_n ?? s?.topN ?? topN) as number,
          totalDates: (s?.total_dates ?? s?.totalDates ?? 0) as number,
        },
        daily: Array.isArray(d.daily)
          ? (d.daily as Record<string, any>[]).map(dd => ({
              date: dd.date as string,
              rankAutocorr: (dd.rank_autocorr ?? dd.rankAutocorr ?? null) as number | null,
              topNRetention: (dd.topN_retention ?? dd.topNRetention ?? null) as number | null,
            }))
          : [],
      } satisfies TurnoverResponse
    }),

  getSectors: (market: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/sectors/${market}`)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    .then(r => {
      const d = r.data as Record<string, any>
      return {
        market: d.market as string,
        totalSymbols: (d.total_symbols ?? d.totalSymbols ?? 0) as number,
        uniqueSectors: (d.unique_sectors ?? d.uniqueSectors ?? 0) as number,
        sectorCounts: (d.sector_counts ?? d.sectorCounts ?? {}) as Record<string, number>,
      } satisfies SectorResponse
    }),

  collectSectors: (market: string) =>
    apiClient.post<Record<string, unknown>>(`/admin/predictions/sectors/${market}/collect`)
    .then(r => r.data),

  getAttribution: (market: string, days = 90, topN = 20) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/attribution`, {
      params: { days, top_n: topN },
    }).then(r => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d = r.data as Record<string, any>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const s = (d.summary ?? {}) as Record<string, any>
      return {
        market: d.market as string,
        days: d.days as number,
        topN: (d.top_n ?? d.topN ?? topN) as number,
        dataPoints: (d.data_points ?? d.dataPoints ?? 0) as number,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        daily: Array.isArray(d.daily) ? (d.daily as Record<string, any>[]).map(dd => ({
          date: dd.date as string,
          portfolioReturn: (dd.portfolio_return ?? dd.portfolioReturn ?? 0) as number,
          universeReturn: (dd.universe_return ?? dd.universeReturn ?? 0) as number,
          sectorAttr: (dd.sector_attr ?? dd.sectorAttr ?? 0) as number,
          sizeAttr: (dd.size_attr ?? dd.sizeAttr ?? 0) as number,
          alpha: (dd.alpha ?? 0) as number,
        })) : [],
        summary: {
          totalReturn: (s.total_return ?? s.totalReturn ?? 0) as number,
          sectorPct: (s.sector_pct ?? s.sectorPct ?? 0) as number,
          sizePct: (s.size_pct ?? s.sizePct ?? 0) as number,
          alphaPct: (s.alpha_pct ?? s.alphaPct ?? 0) as number,
          avgDailyAlpha: (s.avg_daily_alpha ?? s.avgDailyAlpha ?? 0) as number,
          sectorBreakdown: (s.sector_breakdown ?? s.sectorBreakdown ?? {}) as Record<string, number>,
        },
      } satisfies AttributionResponse
    }),

  getPredictionDates: (market: string, nDates = 2, forwardDays = 5) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/prediction-dates`, {
      params: { n_dates: nDates, forward_days: forwardDays },
    }).then(r => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d = r.data as Record<string, any>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const rawPreds = (d.predictions ?? {}) as Record<string, Record<string, any>[]>
      const predictions: Record<string, PredictionResult[]> = {}
      for (const [dt, preds] of Object.entries(rawPreds)) {
        predictions[dt] = preds.map(toCamelPrediction)
      }
      return {
        market: d.market as string,
        forwardDays: (d.forward_days ?? d.forwardDays ?? 5) as number,
        dates: (d.dates ?? []) as string[],
        predictions,
      } satisfies PredictionDatesResponse
    }),
}
