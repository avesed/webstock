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

export interface BacktestConfig {
  cutoffDate: string
  validationDays: number
  forwardDays: number
  configOverride?: Record<string, unknown> | null
  useLlmAgents: boolean
  maxIterations: number
}

export interface BacktestStartResponse {
  taskId: string
  backtestId: string
  market: string
  status: string
}

export interface BacktestTaskStatus {
  taskId: string
  backtestId: string
  market: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  message: string
  currentPhase: string
  currentIteration: number
  maxIterations: number
  iterations: BacktestIteration[]
  elapsedSeconds: number
  createdAt: string | null
  completedAt: string | null
  error?: string | null
}

export interface BacktestIteration {
  iteration: number
  startedAt: string
  completedAt: string
  durationSeconds: number
  phases: Record<string, BacktestPhaseResult>
}

export interface BacktestPhaseResult {
  status: 'completed' | 'failed'
  durationMs?: number
  durationSeconds?: number
  summary?: string
  error?: string
  // Strategist-specific
  configChanges?: string[]
  reasoning?: string
  // Training-specific
  foldIcs?: number[]
  meanIc?: number
  featureCount?: number
  // Inference-specific
  predictionDates?: number
  totalPredictions?: number
  avgSymbolsPerDate?: number
  // Evaluator-specific
  decision?: 'deploy' | 'retry' | 'reject'
  suggestedAdjustments?: Record<string, unknown>
  confidence?: number
  valIc?: number
  valSpread?: number
}

export interface BacktestSummary {
  id: string
  market: string
  cutoffDate: string
  validationDays: number
  forwardDays: number
  status: string
  trainIc: number | null
  trainIcir: number | null
  valIc: number | null
  valIcir: number | null
  valDirectionAccuracy: number | null
  valSpread: number | null
  agentIteration: number | null
  durationSeconds: number | null
  createdAt: string | null
  completedAt: string | null
}

export interface BacktestDetail extends BacktestSummary {
  configOverride: Record<string, unknown> | null
  effectiveConfig: Record<string, unknown>
  trainNdcg: number | null
  foldIcs: number[] | null
  ensembleSize: number | null
  featureCount: number | null
  symbolCount: number | null
  valQ1Return: number | null
  valQ5Return: number | null
  valHitRate: number | null
  valMaxDrawdown: number | null
  results: Record<string, unknown>
  error: string | null
  agentRunId: string | null
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelBacktestTask(t: Record<string, any>): BacktestTaskStatus {
  return {
    taskId: (t.task_id ?? t.taskId) as string,
    backtestId: (t.backtest_id ?? t.backtestId) as string,
    market: t.market as string,
    status: t.status as BacktestTaskStatus['status'],
    progress: (t.progress ?? 0) as number,
    message: (t.message ?? '') as string,
    currentPhase: (t.current_phase ?? t.currentPhase ?? '') as string,
    currentIteration: (t.current_iteration ?? t.currentIteration ?? 0) as number,
    maxIterations: (t.max_iterations ?? t.maxIterations ?? 1) as number,
    iterations: Array.isArray(t.iterations) ? t.iterations.map(toCamelIteration) : [],
    elapsedSeconds: (t.elapsed_seconds ?? t.elapsedSeconds ?? 0) as number,
    createdAt: (t.created_at ?? t.createdAt ?? null) as string | null,
    completedAt: (t.completed_at ?? t.completedAt ?? null) as string | null,
    error: (t.error ?? null) as string | null,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelIteration(it: Record<string, any>): BacktestIteration {
  const phases: Record<string, BacktestPhaseResult> = {}
  if (it.phases) {
    for (const [key, val] of Object.entries(it.phases)) {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const p = val as Record<string, any>
      const phase: BacktestPhaseResult = {
        status: p.status as 'completed' | 'failed',
      }
      const durationMs = (p.duration_ms ?? p.durationMs) as number | undefined
      if (durationMs !== undefined) phase.durationMs = durationMs
      const durationSeconds = (p.duration_seconds ?? p.durationSeconds) as number | undefined
      if (durationSeconds !== undefined) phase.durationSeconds = durationSeconds
      if (p.summary !== undefined) phase.summary = p.summary as string
      if (p.error !== undefined) phase.error = p.error as string
      const configChanges = (p.config_changes ?? p.configChanges) as string[] | undefined
      if (configChanges !== undefined) phase.configChanges = configChanges
      if (p.reasoning !== undefined) phase.reasoning = p.reasoning as string
      const foldIcs = (p.fold_ics ?? p.foldIcs) as number[] | undefined
      if (foldIcs !== undefined) phase.foldIcs = foldIcs
      const meanIc = (p.mean_ic ?? p.meanIc) as number | undefined
      if (meanIc !== undefined) phase.meanIc = meanIc
      const featureCount = (p.feature_count ?? p.featureCount) as number | undefined
      if (featureCount !== undefined) phase.featureCount = featureCount
      const predictionDates = (p.prediction_dates ?? p.predictionDates) as number | undefined
      if (predictionDates !== undefined) phase.predictionDates = predictionDates
      const totalPredictions = (p.total_predictions ?? p.totalPredictions) as number | undefined
      if (totalPredictions !== undefined) phase.totalPredictions = totalPredictions
      const avgSymbolsPerDate = (p.avg_symbols_per_date ?? p.avgSymbolsPerDate) as number | undefined
      if (avgSymbolsPerDate !== undefined) phase.avgSymbolsPerDate = avgSymbolsPerDate
      if (p.decision !== undefined) phase.decision = p.decision as 'deploy' | 'retry' | 'reject'
      const suggestedAdjustments = (p.suggested_adjustments ?? p.suggestedAdjustments) as Record<string, unknown> | undefined
      if (suggestedAdjustments !== undefined) phase.suggestedAdjustments = suggestedAdjustments
      if (p.confidence !== undefined) phase.confidence = p.confidence as number
      const valIc = (p.val_ic ?? p.valIc) as number | undefined
      if (valIc !== undefined) phase.valIc = valIc
      const valSpread = (p.val_spread ?? p.valSpread) as number | undefined
      if (valSpread !== undefined) phase.valSpread = valSpread
      phases[key] = phase
    }
  }
  return {
    iteration: it.iteration as number,
    startedAt: (it.started_at ?? it.startedAt) as string,
    completedAt: (it.completed_at ?? it.completedAt) as string,
    durationSeconds: (it.duration_seconds ?? it.durationSeconds) as number,
    phases,
  }
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function toCamelBacktestSummary(b: Record<string, any>): BacktestSummary {
  return {
    id: String(b.id),
    market: b.market as string,
    cutoffDate: (b.cutoff_date ?? b.cutoffDate) as string,
    validationDays: (b.validation_days ?? b.validationDays) as number,
    forwardDays: (b.forward_days ?? b.forwardDays) as number,
    status: b.status as string,
    trainIc: (b.train_ic ?? b.trainIc ?? null) as number | null,
    trainIcir: (b.train_icir ?? b.trainIcir ?? null) as number | null,
    valIc: (b.val_ic ?? b.valIc ?? null) as number | null,
    valIcir: (b.val_icir ?? b.valIcir ?? null) as number | null,
    valDirectionAccuracy: (b.val_direction_accuracy ?? b.valDirectionAccuracy ?? null) as number | null,
    valSpread: (b.val_spread ?? b.valSpread ?? null) as number | null,
    agentIteration: (b.agent_iteration ?? b.agentIteration ?? null) as number | null,
    durationSeconds: (b.duration_seconds ?? b.durationSeconds ?? null) as number | null,
    createdAt: (b.created_at ?? b.createdAt ?? null) as string | null,
    completedAt: (b.completed_at ?? b.completedAt ?? null) as string | null,
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

  // Backtests
  startBacktest: (market: string, config: BacktestConfig) =>
    apiClient.post<Record<string, unknown>>(`/admin/predictions/${market}/backtest`, {
      cutoff_date: config.cutoffDate,
      validation_days: config.validationDays,
      forward_days: config.forwardDays,
      config_override: config.configOverride ?? null,
      use_llm_agents: config.useLlmAgents,
      max_iterations: config.maxIterations,
    }).then(r => ({
      taskId: (r.data.task_id ?? r.data.taskId) as string,
      backtestId: (r.data.backtest_id ?? r.data.backtestId) as string,
      market: r.data.market as string,
      status: (r.data.status ?? 'pending') as string,
    } satisfies BacktestStartResponse)),

  getBacktestTaskStatus: (taskId: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/backtests/tasks/${taskId}`)
      .then(r => toCamelBacktestTask(r.data)),

  listBacktests: (market: string, limit = 50) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/${market}/backtests`, {
      params: { limit },
    }).then(r => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const d = r.data as Record<string, any>
      const backtests = Array.isArray(d.backtests) ? d.backtests.map(toCamelBacktestSummary) : []
      return { backtests, total: (d.total ?? backtests.length) as number }
    }),

  getBacktestDetail: (backtestId: string) =>
    apiClient.get<Record<string, unknown>>(`/admin/predictions/backtests/${backtestId}`)
      .then(r => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const d = r.data as Record<string, any>
        return {
          ...toCamelBacktestSummary(d),
          configOverride: (d.config_override ?? d.configOverride ?? null) as Record<string, unknown> | null,
          effectiveConfig: (d.effective_config ?? d.effectiveConfig ?? {}) as Record<string, unknown>,
          trainNdcg: (d.train_ndcg ?? d.trainNdcg ?? null) as number | null,
          foldIcs: (d.fold_ics ?? d.foldIcs ?? null) as number[] | null,
          ensembleSize: (d.ensemble_size ?? d.ensembleSize ?? null) as number | null,
          featureCount: (d.feature_count ?? d.featureCount ?? null) as number | null,
          symbolCount: (d.symbol_count ?? d.symbolCount ?? null) as number | null,
          valQ1Return: (d.val_q1_return ?? d.valQ1Return ?? null) as number | null,
          valQ5Return: (d.val_q5_return ?? d.valQ5Return ?? null) as number | null,
          valHitRate: (d.val_hit_rate ?? d.valHitRate ?? null) as number | null,
          valMaxDrawdown: (d.val_max_drawdown ?? d.valMaxDrawdown ?? null) as number | null,
          results: (d.results ?? {}) as Record<string, unknown>,
          error: (d.error ?? null) as string | null,
          agentRunId: (d.agent_run_id ?? d.agentRunId ?? null) as string | null,
        } satisfies BacktestDetail
      }),

  deleteBacktest: (backtestId: string) =>
    apiClient.delete<{ deleted: boolean }>(`/admin/predictions/backtests/${backtestId}`)
      .then(r => r.data),
}
