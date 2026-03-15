import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, RotateCcw, Save } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { cn } from '@/lib/utils'
import { ToggleSwitch, ModelSelectorRow, DEFAULT_MODEL_ASSIGNMENTS, DEFAULT_PHASE2_CONFIG } from './shared'
import type { SystemConfig, ModelAssignmentsConfig, Phase2Config } from '@/types'

const L3_OVERRIDE_KEYS = new Set(['newsEntity', 'newsSentiment', 'newsSummary', 'newsImpact', 'newsReport'])

interface FormState {
  news: SystemConfig['news']
  modelAssignments: ModelAssignmentsConfig
  phase2: Phase2Config
}

export default function SettingsNews() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [formData, setFormData] = useState<FormState>({
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
    modelAssignments: DEFAULT_MODEL_ASSIGNMENTS,
    phase2: DEFAULT_PHASE2_CONFIG,
  })
  const [hasChanges, setHasChanges] = useState(false)

  const { data: config, isLoading, error } = useQuery({
    queryKey: ['admin-system-config'],
    queryFn: adminApi.getSystemConfig,
  })

  const { data: providers = [] } = useQuery({
    queryKey: ['admin-llm-providers'],
    queryFn: adminApi.listLlmProviders,
  })

  const enabledProviders = providers.filter((p) => p.isEnabled)

  useEffect(() => {
    if (config) {
      setFormData({
        news: config.news,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
        phase2: config.phase2 ?? DEFAULT_PHASE2_CONFIG,
      })
      setHasChanges(false)
    }
  }, [config])

  const updateMutation = useMutation({
    mutationFn: adminApi.updateSystemConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-system-config'] })
      toast({ title: t('settings.saved') })
      setHasChanges(false)
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const handleNewsChange = useCallback((key: keyof SystemConfig['news'], value: unknown) => {
    setFormData((prev) => ({
      ...prev,
      news: { ...prev.news, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handleNewsModelChange = useCallback((key: 'newsFilter' | 'contentExtraction', field: 'providerId' | 'model', value: string | null) => {
    setFormData((prev) => ({
      ...prev,
      modelAssignments: {
        ...prev.modelAssignments,
        [key]: {
          ...prev.modelAssignments[key],
          [field]: field === 'providerId' ? value : (value ?? ''),
        },
      },
    }))
    setHasChanges(true)
  }, [])

  const handlePhase2Change = useCallback(<K extends keyof Phase2Config>(key: K, value: Phase2Config[K]) => {
    setFormData((prev) => ({
      ...prev,
      phase2: { ...prev.phase2, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handlePhase2LayerChange = useCallback((
    layer: 'layer1Scoring' | 'layer15Cleaning' | 'layer2Analysis' | 'newsEntity' | 'newsSentiment' | 'newsSummary' | 'newsImpact' | 'newsReport',
    field: 'providerId' | 'model',
    value: string | null
  ) => {
    setFormData((prev) => {
      const phase2 = prev.phase2
      const current = phase2[layer] ?? { providerId: null, model: '' }
      const updated = {
        ...current,
        [field]: field === 'providerId' ? value : (value ?? ''),
      }
      const isL3 = L3_OVERRIDE_KEYS.has(layer)
      const isEmpty = !updated.providerId && !updated.model
      return {
        ...prev,
        phase2: {
          ...phase2,
          [layer]: isL3 && isEmpty ? null : updated,
        },
      }
    })
    setHasChanges(true)
  }, [])

  const handleSave = () => {
    updateMutation.mutate({
      news: formData.news,
      modelAssignments: formData.modelAssignments,
      phase2: formData.phase2,
    } as Partial<SystemConfig>)
  }

  const handleReset = () => {
    if (config) {
      setFormData({
        news: config.news,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
        phase2: config.phase2 ?? DEFAULT_PHASE2_CONFIG,
      })
      setHasChanges(false)
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-64">
          <p className="text-destructive">{tCommon('status.error')}</p>
        </CardContent>
      </Card>
    )
  }

  const phase2 = formData.phase2
  const ma = formData.modelAssignments

  return (
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
                onChange={(e) => handleNewsChange('defaultSource', e.target.value as 'trafilatura' | 'polygon' | 'tavily' | 'playwright')}
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
                onChange={(e) => handleNewsChange('retentionDays', parseInt(e.target.value) || 30)}
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
              onCheckedChange={(checked) => handleNewsChange('autoFetchEnabled', checked)}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="news-finnhub-key">{t('settings.finnhubApiKey')}</Label>
            <Input
              id="news-finnhub-key"
              type="password"
              value={formData.news.finnhubApiKey === '***' ? '' : (formData.news.finnhubApiKey || '')}
              onChange={(e) => handleNewsChange('finnhubApiKey', e.target.value || null)}
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
              onChange={(e) => handleNewsChange('tavilyApiKey', e.target.value || null)}
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
              onCheckedChange={(checked) => handleNewsChange('enableMcpExtraction', checked)}
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
                  label={`${t('settings.models.newsFilterModel')} (${t('settings.models.fallback')})`}
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
                <Label>{t('settings.enableLlmPipeline')}</Label>
                <p className="text-sm text-muted-foreground">{t('settings.enableLlmPipelineDescription')}</p>
              </div>
              <ToggleSwitch
                checked={phase2.enableLlmPipeline}
                onCheckedChange={(checked) => handlePhase2Change('enableLlmPipeline', checked)}
              />
            </div>

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

            {/* Pipeline Layer Models */}
            <div className={cn('space-y-4 transition-opacity', !phase2.enableLlmPipeline && 'opacity-50 pointer-events-none')}>
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
                  {(['layer1Scoring', 'layer15Cleaning', 'layer2Analysis'] as const).map((layer) => (
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

              {/* L3 Per-Agent Model Overrides */}
              <details className="mt-4">
                <summary className="cursor-pointer text-sm font-medium text-muted-foreground hover:text-foreground">
                  {t('settings.phase2.agentModelsTitle' as never)}
                </summary>
                <div className="mt-3 space-y-4 pl-2 border-l-2 border-muted">
                  <p className="text-xs text-muted-foreground">
                    {t('settings.phase2.agentModelsDescription' as never)}
                  </p>
                  {enabledProviders.length > 0 && (
                    <div className="space-y-4">
                      {(['newsEntity', 'newsSummary', 'newsReport'] as const).map((layer) => (
                        <ModelSelectorRow
                          key={layer}
                          label={t(`settings.phase2.${layer}` as never)}
                          providerId={phase2[layer]?.providerId ?? null}
                          model={phase2[layer]?.model ?? ''}
                          providers={enabledProviders}
                          onProviderChange={(id) => handlePhase2LayerChange(layer, 'providerId', id)}
                          onModelChange={(m) => handlePhase2LayerChange(layer, 'model', m)}
                          t={t}
                        />
                      ))}
                    </div>
                  )}
                </div>
              </details>
            </div>
          </div>

        </CardContent>
      </Card>

      {/* Save/Reset */}
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={handleReset} disabled={!hasChanges || updateMutation.isPending}>
          <RotateCcw className="mr-2 h-4 w-4" />
          {tCommon('actions.reset')}
        </Button>
        <Button onClick={handleSave} disabled={!hasChanges || updateMutation.isPending}>
          {updateMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Save className="mr-2 h-4 w-4" />
          )}
          {tCommon('actions.save')}
        </Button>
      </div>
    </div>
  )
}
