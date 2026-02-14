import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, RotateCcw, Save, Info } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { LlmProviders } from './LlmProviders'
import { ModelAssignments } from './ModelAssignments'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { cn } from '@/lib/utils'
import type { SystemConfig, ModelAssignmentsConfig, LlmProvider, Phase2Config } from '@/types'

const DEFAULT_MODEL_ASSIGNMENTS: ModelAssignmentsConfig = {
  chat: { providerId: null, model: 'gpt-4o-mini' },
  analysis: { providerId: null, model: 'gpt-4o-mini' },
  synthesis: { providerId: null, model: 'gpt-4o' },
  embedding: { providerId: null, model: 'text-embedding-3-small' },
  newsFilter: { providerId: null, model: 'gpt-4o-mini' },
  contentExtraction: { providerId: null, model: 'gpt-4o-mini' },
}

const DEFAULT_PHASE2_CONFIG: Phase2Config = {
  enabled: false,
  scoreThreshold: 50,
  discardThreshold: 105,
  fullAnalysisThreshold: 195,
  layer1Scoring: { providerId: null, model: 'gpt-4o-mini' },
  layer15Cleaning: { providerId: null, model: 'gpt-4o' },
  layer2Scoring: { providerId: null, model: 'gpt-4o-mini' },
  layer2Analysis: { providerId: null, model: 'gpt-4o' },
  layer2Lightweight: { providerId: null, model: 'gpt-4o-mini' },
  highValueSources: ['reuters', 'bloomberg', 'sec', 'company_announcement'],
  highValuePct: 0.20,
  cacheEnabled: true,
  cacheTtlMinutes: 60,
}

const DEFAULT_CONFIG: SystemConfig = {
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
    enableLlmPipeline: false,
    enableMcpExtraction: false,
  },
  modelAssignments: DEFAULT_MODEL_ASSIGNMENTS,
  phase2: DEFAULT_PHASE2_CONFIG,
}

interface ToggleSwitchProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}

function ToggleSwitch({ checked, onCheckedChange, disabled }: ToggleSwitchProps) {
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

/** Inline provider+model selector row */
function ModelSelectorRow({
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
          // Auto-select first model of new provider
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

export function SystemSettings() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  // State
  const [formData, setFormData] = useState<SystemConfig>(DEFAULT_CONFIG)
  const [hasNewsChanges, setHasNewsChanges] = useState(false)
  const [hasFeaturesChanges, setHasFeaturesChanges] = useState(false)
  const [hasModelChanges, setHasModelChanges] = useState(false)

  // Queries
  const { data: config, isLoading: isConfigLoading, error: configError } = useQuery({
    queryKey: ['admin-system-config'],
    queryFn: adminApi.getSystemConfig,
  })

  const { data: providers = [] } = useQuery({
    queryKey: ['admin-llm-providers'],
    queryFn: adminApi.listLlmProviders,
  })

  const enabledProviders = providers.filter((p) => p.isEnabled)

  // Sync form data with fetched config
  useEffect(() => {
    if (config) {
      setFormData({
        ...config,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
        phase2: config.phase2 ?? DEFAULT_PHASE2_CONFIG,
      })
      setHasNewsChanges(false)
      setHasFeaturesChanges(false)
      setHasModelChanges(false)
    }
  }, [config])

  // Mutations
  const updateMutation = useMutation({
    mutationFn: adminApi.updateSystemConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
      toast({ title: t('settings.saved') })
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  // Handlers
  const handleChange = <K extends keyof SystemConfig>(
    section: K,
    key: keyof SystemConfig[K],
    value: SystemConfig[K][keyof SystemConfig[K]]
  ) => {
    setFormData((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [key]: value,
      },
    }))
    if (section === 'news') setHasNewsChanges(true)
    if (section === 'features') setHasFeaturesChanges(true)
    if (section === 'llm') setHasModelChanges(true)
  }

  // Model assignment changes for Models tab (analysis, synthesis, chat, embedding)
  const handleModelAssignmentsChange = useCallback((assignments: ModelAssignmentsConfig) => {
    setFormData((prev) => ({
      ...prev,
      modelAssignments: assignments,
    }))
    setHasModelChanges(true)
  }, [])

  // Model assignment changes from News tab (newsFilter, contentExtraction)
  const handleNewsModelChange = useCallback((key: 'newsFilter' | 'contentExtraction', field: 'providerId' | 'model', value: string | null) => {
    setFormData((prev) => ({
      ...prev,
      modelAssignments: {
        ...(prev.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS),
        [key]: {
          ...(prev.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS)[key],
          [field]: field === 'providerId' ? value : (value ?? ''),
        },
      },
    }))
    setHasNewsChanges(true)
  }, [])

  // Phase 2 config changes (from News tab)
  const handlePhase2Change = useCallback(<K extends keyof Phase2Config>(key: K, value: Phase2Config[K]) => {
    setFormData((prev) => ({
      ...prev,
      phase2: {
        ...(prev.phase2 ?? DEFAULT_PHASE2_CONFIG),
        [key]: value,
      },
    }))
    setHasNewsChanges(true)
  }, [])

  // Phase 2 layer model changes (from News tab)
  const handlePhase2LayerChange = useCallback((
    layer: 'layer1Scoring' | 'layer15Cleaning' | 'layer2Scoring' | 'layer2Analysis' | 'layer2Lightweight',
    field: 'providerId' | 'model',
    value: string | null
  ) => {
    setFormData((prev) => {
      const phase2 = prev.phase2 ?? DEFAULT_PHASE2_CONFIG
      return {
        ...prev,
        phase2: {
          ...phase2,
          [layer]: {
            ...phase2[layer],
            [field]: field === 'providerId' ? value : (value ?? ''),
          },
        },
      }
    })
    setHasNewsChanges(true)
  }, [])

  // Cache changes (from Models tab)
  const handleCacheChange = useCallback(<K extends 'cacheEnabled' | 'cacheTtlMinutes'>(key: K, value: Phase2Config[K]) => {
    setFormData((prev) => ({
      ...prev,
      phase2: {
        ...(prev.phase2 ?? DEFAULT_PHASE2_CONFIG),
        [key]: value,
      },
    }))
    setHasModelChanges(true)
  }, [])

  // Per-tab save handlers — each sends the full modelAssignments + phase2 snapshot
  const handleSaveModels = () => {
    updateMutation.mutate({
      llm: formData.llm,
      modelAssignments: formData.modelAssignments,
      phase2: formData.phase2,
    } as SystemConfig, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
        toast({ title: t('settings.saved') })
        setHasModelChanges(false)
      },
    })
  }

  const handleSaveNews = () => {
    updateMutation.mutate({
      news: formData.news,
      modelAssignments: formData.modelAssignments,
      phase2: formData.phase2,
    } as Partial<SystemConfig>, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
        toast({ title: t('settings.saved') })
        setHasNewsChanges(false)
      },
    })
  }

  const handleSaveFeatures = () => {
    updateMutation.mutate({ features: formData.features } as Partial<SystemConfig>, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
        toast({ title: t('settings.saved') })
        setHasFeaturesChanges(false)
      },
    })
  }

  // Per-tab reset handlers
  const handleResetModels = () => {
    if (config) {
      setFormData((prev) => ({
        ...prev,
        llm: config.llm,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
        phase2: {
          ...(prev.phase2 ?? DEFAULT_PHASE2_CONFIG),
          cacheEnabled: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).cacheEnabled,
          cacheTtlMinutes: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).cacheTtlMinutes,
        },
      }))
      setHasModelChanges(false)
    }
  }

  const handleResetNews = () => {
    if (config) {
      setFormData((prev) => ({
        ...prev,
        news: config.news,
        modelAssignments: {
          ...(prev.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS),
          newsFilter: (config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS).newsFilter,
          contentExtraction: (config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS).contentExtraction,
        },
        phase2: {
          ...(prev.phase2 ?? DEFAULT_PHASE2_CONFIG),
          enabled: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).enabled,
          scoreThreshold: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).scoreThreshold,
          discardThreshold: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).discardThreshold,
          fullAnalysisThreshold: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).fullAnalysisThreshold,
          layer1Scoring: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).layer1Scoring,
          layer15Cleaning: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).layer15Cleaning,
          layer2Scoring: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).layer2Scoring,
          layer2Analysis: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).layer2Analysis,
          layer2Lightweight: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).layer2Lightweight,
          highValueSources: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).highValueSources,
          highValuePct: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).highValuePct,
        },
      }))
      setHasNewsChanges(false)
    }
  }

  const handleResetFeatures = () => {
    if (config) {
      setFormData((prev) => ({
        ...prev,
        features: config.features,
      }))
      setHasFeaturesChanges(false)
    }
  }

  const handleRefreshProviders = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['admin-llm-providers'] })
  }, [queryClient])

  if (isConfigLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (configError) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64">
          <p className="text-destructive">{tCommon('status.error')}</p>
        </CardContent>
      </Card>
    )
  }

  const phase2 = formData.phase2 ?? DEFAULT_PHASE2_CONFIG
  const ma = formData.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS

  return (
    <TooltipProvider>
      <Tabs defaultValue="providers" className="space-y-4">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="providers">{t('settings.tabProviders')}</TabsTrigger>
          <TabsTrigger value="models">{t('settings.tabModels')}</TabsTrigger>
          <TabsTrigger value="news">{t('settings.tabNews')}</TabsTrigger>
          <TabsTrigger value="features">{t('settings.tabFeatures')}</TabsTrigger>
        </TabsList>

        {/* LLM Providers Tab */}
        <TabsContent value="providers">
          <LlmProviders
            providers={providers}
            onRefresh={handleRefreshProviders}
          />
        </TabsContent>

        {/* Model Configuration Tab */}
        <TabsContent value="models">
          <div className="space-y-6">
            <ModelAssignments
              providers={providers}
              assignments={ma}
              onAssignmentsChange={handleModelAssignmentsChange}
              advancedSettings={{
                maxClarificationRounds: formData.llm.maxClarificationRounds,
                clarificationConfidenceThreshold: formData.llm.clarificationConfidenceThreshold,
              }}
              onAdvancedChange={(key, value) => handleChange('llm', key as keyof SystemConfig['llm'], value as never)}
            />

            <Separator />

            {/* Prompt Cache Config */}
            <div className="space-y-4">
              <div className="space-y-1">
                <h4 className="text-sm font-medium">{t('settings.phase2.cacheTitle')}</h4>
                <p className="text-sm text-muted-foreground">{t('settings.phase2.cacheDescription')}</p>
              </div>

              <div className="flex items-center justify-between">
                <Label>{t('settings.phase2.cacheEnabled')}</Label>
                <ToggleSwitch
                  checked={phase2.cacheEnabled}
                  onCheckedChange={(checked) => handleCacheChange('cacheEnabled', checked)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="cache-ttl">{t('settings.phase2.cacheTtlMinutes')}</Label>
                <Input
                  id="cache-ttl"
                  type="number"
                  min={1}
                  max={1440}
                  value={phase2.cacheTtlMinutes}
                  onChange={(e) => handleCacheChange('cacheTtlMinutes', parseInt(e.target.value) || 60)}
                />
                <p className="text-xs text-muted-foreground">{t('settings.phase2.cacheTtlMinutesHint')}</p>
              </div>
            </div>

            {/* Save/Reset for Model Configuration */}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleResetModels} disabled={!hasModelChanges || updateMutation.isPending}>
                <RotateCcw className="mr-2 h-4 w-4" />
                {tCommon('actions.reset')}
              </Button>
              <Button onClick={handleSaveModels} disabled={!hasModelChanges || updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                {tCommon('actions.save')}
              </Button>
            </div>
          </div>
        </TabsContent>

        {/* News Processing Tab */}
        <TabsContent value="news">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>{t('settings.newsTitle')}</CardTitle>
                <CardDescription>{t('settings.newsDescription')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="news-source">{t('settings.defaultSource')}</Label>
                    <select
                      id="news-source"
                      value={formData.news.defaultSource}
                      onChange={(e) => handleChange('news', 'defaultSource', e.target.value as 'trafilatura' | 'polygon' | 'tavily' | 'playwright')}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      <option value="trafilatura">{t('settings.sourceTrafilatura')}</option>
                      <option value="polygon">{t('settings.sourcePolygon')}</option>
                      <option value="tavily">{t('settings.sourceTavily')}</option>
                      <option value="playwright">{t('settings.sourcePlaywright')}</option>
                    </select>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="news-retention">{t('settings.retentionDays')}</Label>
                    <Input
                      id="news-retention"
                      type="number"
                      min={1}
                      max={365}
                      value={formData.news.retentionDays}
                      onChange={(e) => handleChange('news', 'retentionDays', parseInt(e.target.value) || 30)}
                    />
                  </div>
                </div>

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.autoFetch')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.autoFetchDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.news.autoFetchEnabled}
                    onCheckedChange={(checked) => handleChange('news', 'autoFetchEnabled', checked)}
                  />
                </div>

                <div className="space-y-2">
                  <Label htmlFor="news-finnhub-key">{t('settings.finnhubApiKey')}</Label>
                  <Input
                    id="news-finnhub-key"
                    type="password"
                    value={formData.news.finnhubApiKey === '***' ? '' : (formData.news.finnhubApiKey || '')}
                    onChange={(e) => handleChange('news', 'finnhubApiKey', e.target.value || null)}
                    placeholder={formData.news.finnhubApiKey === '***' ? t('settings.apiKeySet') : t('settings.apiKeyPlaceholder')}
                  />
                  {formData.news.finnhubApiKey === '***' && (
                    <p className="text-xs text-muted-foreground">{t('settings.apiKeySetHint')}</p>
                  )}
                  <p className="text-xs text-muted-foreground">{t('settings.finnhubApiKeyHint')}</p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="news-tavily-key">{t('settings.tavilyApiKey')}</Label>
                  <Input
                    id="news-tavily-key"
                    type="password"
                    value={formData.news.tavilyApiKey === '***' ? '' : (formData.news.tavilyApiKey || '')}
                    onChange={(e) => handleChange('news', 'tavilyApiKey', e.target.value || null)}
                    placeholder={formData.news.tavilyApiKey === '***' ? t('settings.apiKeySet') : t('settings.apiKeyPlaceholder')}
                  />
                  {formData.news.tavilyApiKey === '***' && (
                    <p className="text-xs text-muted-foreground">{t('settings.apiKeySetHint')}</p>
                  )}
                  <p className="text-xs text-muted-foreground">{t('settings.tavilyApiKeyHint')}</p>
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.enableMcpExtraction')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.enableMcpExtractionDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.news.enableMcpExtraction}
                    onCheckedChange={(checked) => handleChange('news', 'enableMcpExtraction', checked)}
                  />
                </div>

                <Separator />

                {/* News Processing Models */}
                <div className="space-y-4">
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">{t('settings.newsModelsTitle')}</h4>
                    <p className="text-sm text-muted-foreground">{t('settings.newsModelsDescription')}</p>
                  </div>

                  {enabledProviders.length === 0 ? (
                    <p className="text-sm text-muted-foreground italic">
                      {t('settings.models.noProviders')}
                    </p>
                  ) : (
                    <div className="space-y-4">
                      <ModelSelectorRow
                        label={t('settings.models.newsFilterModel')}
                        providerId={ma.newsFilter.providerId}
                        model={ma.newsFilter.model}
                        providers={enabledProviders}
                        onProviderChange={(id) => handleNewsModelChange('newsFilter', 'providerId', id)}
                        onModelChange={(m) => handleNewsModelChange('newsFilter', 'model', m)}
                        t={t}
                      />
                      <ModelSelectorRow
                        label={t('settings.models.contentExtractionModel')}
                        providerId={ma.contentExtraction.providerId}
                        model={ma.contentExtraction.model}
                        providers={enabledProviders}
                        onProviderChange={(id) => handleNewsModelChange('contentExtraction', 'providerId', id)}
                        onModelChange={(m) => handleNewsModelChange('contentExtraction', 'model', m)}
                        t={t}
                      />
                    </div>
                  )}
                </div>

                <Separator />

                {/* Multi-Agent News Analysis (Phase 2) */}
                <div className="space-y-4">
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">{t('settings.phase2.title')}</h4>
                    <p className="text-sm text-muted-foreground">{t('settings.phase2.description')}</p>
                  </div>

                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>{t('settings.phase2.enabled')}</Label>
                      <p className="text-sm text-muted-foreground">{t('settings.phase2.enabledDescription')}</p>
                    </div>
                    <ToggleSwitch
                      checked={phase2.enabled}
                      onCheckedChange={(checked) => handlePhase2Change('enabled', checked)}
                    />
                  </div>

                  <div className="grid gap-4 sm:grid-cols-2">
                    <div className="space-y-2">
                      <Label htmlFor="phase2-discard-threshold">{t('settings.phase2.discardThreshold')}</Label>
                      <Input
                        id="phase2-discard-threshold"
                        type="number"
                        min={0}
                        max={300}
                        value={phase2.discardThreshold}
                        onChange={(e) => handlePhase2Change('discardThreshold', parseInt(e.target.value) || 0)}
                      />
                      <p className="text-xs text-muted-foreground">{t('settings.phase2.discardThresholdHint')}</p>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="phase2-full-analysis-threshold">{t('settings.phase2.fullAnalysisThreshold')}</Label>
                      <Input
                        id="phase2-full-analysis-threshold"
                        type="number"
                        min={0}
                        max={300}
                        value={phase2.fullAnalysisThreshold}
                        onChange={(e) => handlePhase2Change('fullAnalysisThreshold', parseInt(e.target.value) || 0)}
                      />
                      <p className="text-xs text-muted-foreground">{t('settings.phase2.fullAnalysisThresholdHint')}</p>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="phase2-score-threshold">{t('settings.phase2.scoreThreshold')}</Label>
                    <Input
                      id="phase2-score-threshold"
                      type="number"
                      min={0}
                      max={100}
                      value={phase2.scoreThreshold}
                      onChange={(e) => handlePhase2Change('scoreThreshold', parseInt(e.target.value) || 0)}
                    />
                    <p className="text-xs text-muted-foreground">{t('settings.phase2.scoreThresholdHint')}</p>
                  </div>

                  {/* Phase 2 Layer Models (dimmed when disabled) */}
                  <div className={cn('space-y-4 transition-opacity', !phase2.enabled && 'opacity-50 pointer-events-none')}>
                    <div className="space-y-1">
                      <h4 className="text-sm font-medium">{t('settings.phase2.modelsTitle')}</h4>
                      <p className="text-sm text-muted-foreground">{t('settings.phase2.modelsDescription')}</p>
                    </div>

                    {enabledProviders.length === 0 ? (
                      <p className="text-sm text-muted-foreground italic">
                        {t('settings.models.noProviders')}
                      </p>
                    ) : (
                      <div className="space-y-4">
                        {(['layer1Scoring', 'layer15Cleaning', 'layer2Scoring', 'layer2Analysis', 'layer2Lightweight'] as const).map((layer) => (
                          <ModelSelectorRow
                            key={layer}
                            label={t(`settings.phase2.${layer}` as never)}
                            providerId={phase2[layer].providerId}
                            model={phase2[layer].model}
                            providers={enabledProviders}
                            onProviderChange={(id) => handlePhase2LayerChange(layer, 'providerId', id)}
                            onModelChange={(m) => handlePhase2LayerChange(layer, 'model', m)}
                            t={t}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>

                <Separator />

                {/* Source Tiering (dimmed when Phase 2 disabled) */}
                <div className={cn('space-y-4 transition-opacity', !phase2.enabled && 'opacity-50 pointer-events-none')}>
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">{t('settings.phase2.sourceTieringTitle')}</h4>
                    <p className="text-sm text-muted-foreground">{t('settings.phase2.sourceTieringDescription')}</p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="phase2-high-value-sources">{t('settings.phase2.highValueSources')}</Label>
                    <Input
                      id="phase2-high-value-sources"
                      value={phase2.highValueSources?.join(', ') ?? ''}
                      onChange={(e) =>
                        handlePhase2Change('highValueSources', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))
                      }
                    />
                    <p className="text-xs text-muted-foreground">{t('settings.phase2.highValueSourcesHint')}</p>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="phase2-high-value-pct">{t('settings.phase2.highValuePct')}</Label>
                    <Input
                      id="phase2-high-value-pct"
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={phase2.highValuePct}
                      onChange={(e) => handlePhase2Change('highValuePct', parseFloat(e.target.value) || 0)}
                    />
                    <p className="text-xs text-muted-foreground">{t('settings.phase2.highValuePctHint')}</p>
                  </div>
                </div>

              </CardContent>
            </Card>

            {/* Save/Reset for News */}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleResetNews} disabled={!hasNewsChanges || updateMutation.isPending}>
                <RotateCcw className="mr-2 h-4 w-4" />
                {tCommon('actions.reset')}
              </Button>
              <Button onClick={handleSaveNews} disabled={!hasNewsChanges || updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                {tCommon('actions.save')}
              </Button>
            </div>
          </div>
        </TabsContent>

        {/* Feature Toggles Tab */}
        <TabsContent value="features">
          <div className="space-y-6">
            <Card>
              <CardHeader>
                <CardTitle>{t('settings.featuresTitle')}</CardTitle>
                <CardDescription>{t('settings.featuresDescription')}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="space-y-0.5">
                      <Label>{t('settings.allowUserApiKeys')}</Label>
                      <p className="text-sm text-muted-foreground">{t('settings.allowUserApiKeysDescription')}</p>
                    </div>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Info className="h-4 w-4 text-muted-foreground" />
                      </TooltipTrigger>
                      <TooltipContent>{t('settings.allowUserApiKeysTooltip')}</TooltipContent>
                    </Tooltip>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.allowUserApiKeys}
                    onCheckedChange={(checked) => handleChange('features', 'allowUserApiKeys', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.allowUserCustomModels')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.allowUserCustomModelsDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.allowUserCustomModels}
                    onCheckedChange={(checked) => handleChange('features', 'allowUserCustomModels', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.enableNewsAnalysis')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.enableNewsAnalysisDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.enableNewsAnalysis}
                    onCheckedChange={(checked) => handleChange('features', 'enableNewsAnalysis', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.enableStockAnalysis')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.enableStockAnalysisDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.enableStockAnalysis}
                    onCheckedChange={(checked) => handleChange('features', 'enableStockAnalysis', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.requireApproval')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.requireApprovalDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.requireRegistrationApproval}
                    onCheckedChange={(checked) => handleChange('features', 'requireRegistrationApproval', checked)}
                  />
                </div>

                <Separator />

                <div className="flex items-center justify-between">
                  <div className="space-y-0.5">
                    <Label>{t('settings.enableLlmPipeline')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.enableLlmPipelineDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.enableLlmPipeline}
                    onCheckedChange={(checked) => handleChange('features', 'enableLlmPipeline', checked)}
                  />
                </div>
              </CardContent>
            </Card>

            {/* Save/Reset for Features */}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleResetFeatures} disabled={!hasFeaturesChanges || updateMutation.isPending}>
                <RotateCcw className="mr-2 h-4 w-4" />
                {tCommon('actions.reset')}
              </Button>
              <Button onClick={handleSaveFeatures} disabled={!hasFeaturesChanges || updateMutation.isPending}>
                {updateMutation.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Save className="mr-2 h-4 w-4" />
                )}
                {tCommon('actions.save')}
              </Button>
            </div>
          </div>
        </TabsContent>
      </Tabs>
    </TooltipProvider>
  )
}
