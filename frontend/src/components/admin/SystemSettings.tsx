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
import type { SystemConfig, ModelAssignmentsConfig } from '@/types'

const DEFAULT_MODEL_ASSIGNMENTS: ModelAssignmentsConfig = {
  chat: { providerId: null, model: 'gpt-4o-mini' },
  analysis: { providerId: null, model: 'gpt-4o-mini' },
  synthesis: { providerId: null, model: 'gpt-4o' },
  embedding: { providerId: null, model: 'text-embedding-3-small' },
  newsFilter: { providerId: null, model: 'gpt-4o-mini' },
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
    defaultSource: 'scraper',
    retentionDays: 30,
    embeddingModel: 'text-embedding-3-small',
    filterModel: 'gpt-4o-mini',
    autoFetchEnabled: true,
    finnhubApiKey: null,
  },
  features: {
    allowUserApiKeys: true,
    allowUserCustomModels: false,
    enableNewsAnalysis: true,
    enableStockAnalysis: true,
    requireRegistrationApproval: false,
    useTwoPhaseFilter: false,
  },
  modelAssignments: DEFAULT_MODEL_ASSIGNMENTS,
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

  // Sync form data with fetched config
  useEffect(() => {
    if (config) {
      setFormData({
        ...config,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
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

  const handleModelAssignmentsChange = useCallback((assignments: ModelAssignmentsConfig) => {
    setFormData((prev) => ({
      ...prev,
      modelAssignments: assignments,
    }))
    setHasModelChanges(true)
  }, [])

  // Per-tab save handlers
  const handleSaveModels = () => {
    updateMutation.mutate({
      llm: formData.llm,
      modelAssignments: formData.modelAssignments,
    } as SystemConfig, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
        toast({ title: t('settings.saved') })
        setHasModelChanges(false)
      },
    })
  }

  const handleSaveNews = () => {
    updateMutation.mutate({ news: formData.news } as Partial<SystemConfig>, {
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
      }))
      setHasModelChanges(false)
    }
  }

  const handleResetNews = () => {
    if (config) {
      setFormData((prev) => ({
        ...prev,
        news: config.news,
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
              assignments={formData.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS}
              onAssignmentsChange={handleModelAssignmentsChange}
              advancedSettings={{
                maxClarificationRounds: formData.llm.maxClarificationRounds,
                clarificationConfidenceThreshold: formData.llm.clarificationConfidenceThreshold,
              }}
              onAdvancedChange={(key, value) => handleChange('llm', key as keyof SystemConfig['llm'], value as never)}
            />

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
                      onChange={(e) => handleChange('news', 'defaultSource', e.target.value as 'scraper' | 'polygon')}
                      className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                    >
                      <option value="scraper">{t('settings.sourceScraper')}</option>
                      <option value="polygon">{t('settings.sourcePolygon')}</option>
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

                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="news-embedding-model">{t('settings.embeddingModel')}</Label>
                    <Input
                      id="news-embedding-model"
                      value={formData.news.embeddingModel}
                      onChange={(e) => handleChange('news', 'embeddingModel', e.target.value)}
                      placeholder="text-embedding-3-small"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="news-filter-model">{t('settings.filterModel')}</Label>
                    <Input
                      id="news-filter-model"
                      value={formData.news.filterModel}
                      onChange={(e) => handleChange('news', 'filterModel', e.target.value)}
                      placeholder="gpt-4o-mini"
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
                    <Label>{t('settings.useTwoPhaseFilter')}</Label>
                    <p className="text-sm text-muted-foreground">{t('settings.useTwoPhaseFilterDescription')}</p>
                  </div>
                  <ToggleSwitch
                    checked={formData.features.useTwoPhaseFilter}
                    onCheckedChange={(checked) => handleChange('features', 'useTwoPhaseFilter', checked)}
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
