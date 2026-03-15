import { cn } from '@/lib/utils'
import { Label } from '@/components/ui/label'
import type { SystemConfig, ModelAssignmentsConfig, Phase2Config, DiscussionConfig, PredictionConfig, LlmProvider } from '@/types'

// ── Default constants ──────────────────────────

export const DEFAULT_MODEL_ASSIGNMENTS: ModelAssignmentsConfig = {
  chat: { providerId: null, model: 'gpt-4o-mini' },
  analysis: { providerId: null, model: 'gpt-4o-mini' },
  synthesis: { providerId: null, model: 'gpt-4o' },
  embedding: { providerId: null, model: 'text-embedding-3-small' },
  newsFilter: { providerId: null, model: 'gpt-4o-mini' },
  contentExtraction: { providerId: null, model: 'gpt-4o-mini' },
}

export const DEFAULT_PHASE2_CONFIG: Phase2Config = {
  enableLlmPipeline: false,
  discardThreshold: 105,
  layer1Scoring: { providerId: null, model: 'gpt-4o-mini' },
  layer15Cleaning: { providerId: null, model: 'gpt-4o' },
  layer2Scoring: { providerId: null, model: 'gpt-4o-mini' },
  layer2Analysis: { providerId: null, model: 'gpt-4o' },
  newsEntity: null,
  newsSentiment: null,
  newsSummary: null,
  newsImpact: null,
  newsReport: null,
  cacheEnabled: true,
  cacheTtlMinutes: 60,
}

export const DEFAULT_DISCUSSION_CONFIG: DiscussionConfig = {
  enabled: false,
  maxRounds: 3,
  providerId: null,
  model: 'gpt-4o',
}

export const DEFAULT_PREDICTION_CONFIG: PredictionConfig = {
  enabled: false,
  providerId: null,
  model: 'gpt-4o-mini',
  autoRetrainEnabled: false,
  autoRetrainIntervalDays: 7,
  autoTuneEnabled: false,
  autoTuneIntervalDays: 30,
  autoTuneMaxIterations: 3,
}

export const DEFAULT_CONFIG: SystemConfig = {
  llm: {
    apiKey: null,
    baseUrl: 'https://api.openai.com/v1',
    useLocalModels: false,
    localLlmBaseUrl: null,
    analysisModel: 'gpt-4o-mini',
    synthesisModel: 'gpt-4o',
    maxClarificationRounds: 2,
    clarificationConfidenceThreshold: 0.6,
    anthropicApiKey: null,
    anthropicBaseUrl: null,
  },
  news: {
    defaultSource: 'trafilatura',
    retentionDays: 30,
    embeddingModel: 'text-embedding-3-small',
    filterModel: 'gpt-4o-mini',
    autoFetchEnabled: true,
    finnhubApiKey: null,
    tavilyApiKey: null,
    enableMcpExtraction: false,
  },
  features: {
    allowUserApiKeys: true,
    allowUserCustomModels: false,
    enableNewsAnalysis: true,
    enableStockAnalysis: true,
    requireRegistrationApproval: false,
    enableMcpExtraction: false,
  },
  modelAssignments: DEFAULT_MODEL_ASSIGNMENTS,
  phase2: DEFAULT_PHASE2_CONFIG,
  discussion: DEFAULT_DISCUSSION_CONFIG,
  prediction: DEFAULT_PREDICTION_CONFIG,
}

// ── Shared components ──────────────────────────

interface ToggleSwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}

export function ToggleSwitch({ checked, onCheckedChange, disabled }: ToggleSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
        'disabled:cursor-not-allowed disabled:opacity-50',
        checked ? 'bg-primary' : 'bg-input'
      )}
    >
      <span
        className={cn(
          'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-background shadow-lg ring-0 transition-transform',
          checked ? 'translate-x-5' : 'translate-x-0'
        )}
      />
    </button>
  )
}

export function ModelSelectorRow({
  label,
  providerId,
  model,
  providers,
  onProviderChange,
  onModelChange,
  disabled,
  t,
}: {
  label: string
  providerId: string | null
  model: string
  providers: LlmProvider[]
  onProviderChange: (id: string | null) => void
  onModelChange: (model: string) => void
  disabled?: boolean
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  t: (key: any) => string
}) {
  const selectedProvider = providerId ? providers.find((p) => p.id === providerId) : undefined
  const availableModels = selectedProvider?.models ?? []

  return (
    <div className={cn('grid gap-4 sm:grid-cols-[140px_1fr_1fr] items-center', disabled && 'opacity-50 pointer-events-none')}>
      <Label className="text-sm">{label}</Label>
      <select
        value={providerId ?? ''}
        onChange={(e) => {
          const newId = e.target.value || null
          onProviderChange(newId)
          if (newId) {
            const newProvider = providers.find((p) => p.id === newId)
            onModelChange(newProvider?.models[0] ?? '')
          } else {
            onModelChange('')
          }
        }}
        className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
      >
        <option value="">{t('settings.models.selectProvider')}</option>
        {providers.map((provider) => (
          <option key={provider.id} value={provider.id}>{provider.name}</option>
        ))}
      </select>
      <select
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
        disabled={!providerId || availableModels.length === 0}
        className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">{t('settings.models.selectModel')}</option>
        {availableModels.map((m) => (
          <option key={m} value={m}>{m}</option>
        ))}
      </select>
    </div>
  )
}
