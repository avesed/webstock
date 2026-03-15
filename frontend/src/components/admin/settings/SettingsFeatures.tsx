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
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { cn } from '@/lib/utils'
import { ToggleSwitch, ModelSelectorRow, DEFAULT_DISCUSSION_CONFIG, DEFAULT_PREDICTION_CONFIG } from './shared'
import type { SystemConfig, DiscussionConfig, PredictionConfig } from '@/types'

interface FormState {
  features: SystemConfig['features']
  discussion: DiscussionConfig
  prediction: PredictionConfig
}

export default function SettingsFeatures() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [formData, setFormData] = useState<FormState>({
    features: {
      allowUserApiKeys: true,
      allowUserCustomModels: false,
      enableNewsAnalysis: true,
      enableStockAnalysis: true,
      requireRegistrationApproval: false,
      enableMcpExtraction: false,
    },
    discussion: DEFAULT_DISCUSSION_CONFIG,
    prediction: DEFAULT_PREDICTION_CONFIG,
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
        features: config.features,
        discussion: config.discussion ?? DEFAULT_DISCUSSION_CONFIG,
        prediction: config.prediction ?? DEFAULT_PREDICTION_CONFIG,
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

  const handleFeatureChange = useCallback((key: keyof SystemConfig['features'], value: boolean) => {
    setFormData((prev) => ({
      ...prev,
      features: { ...prev.features, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handleDiscussionChange = useCallback(<K extends keyof DiscussionConfig>(key: K, value: DiscussionConfig[K]) => {
    setFormData((prev) => ({
      ...prev,
      discussion: { ...prev.discussion, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handlePredictionChange = useCallback((key: keyof PredictionConfig, value: unknown) => {
    setFormData((prev) => ({
      ...prev,
      prediction: { ...prev.prediction, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handleSave = () => {
    updateMutation.mutate({
      features: formData.features,
      discussion: formData.discussion,
      prediction: formData.prediction,
    } as Partial<SystemConfig>)
  }

  const handleReset = () => {
    if (config) {
      setFormData({
        features: config.features,
        discussion: config.discussion ?? DEFAULT_DISCUSSION_CONFIG,
        prediction: config.prediction ?? DEFAULT_PREDICTION_CONFIG,
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

  return (
    <TooltipProvider>
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
                onCheckedChange={(checked) => handleFeatureChange('allowUserApiKeys', checked)}
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
                onCheckedChange={(checked) => handleFeatureChange('allowUserCustomModels', checked)}
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
                onCheckedChange={(checked) => handleFeatureChange('enableNewsAnalysis', checked)}
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
                onCheckedChange={(checked) => handleFeatureChange('enableStockAnalysis', checked)}
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
                onCheckedChange={(checked) => handleFeatureChange('requireRegistrationApproval', checked)}
              />
            </div>

            <Separator />

            {/* Discussion Group Settings */}
            <div className="space-y-4">
              <div className="space-y-1">
                <h4 className="text-sm font-medium">{t('settings.discussion.title')}</h4>
                <p className="text-sm text-muted-foreground">{t('settings.discussion.description')}</p>
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('settings.discussion.enabled')}</Label>
                  <p className="text-sm text-muted-foreground">{t('settings.discussion.enabledDescription')}</p>
                </div>
                <ToggleSwitch
                  checked={formData.discussion.enabled}
                  onCheckedChange={(checked) => handleDiscussionChange('enabled', checked)}
                />
              </div>

              <div className={cn('space-y-4 transition-opacity', !formData.discussion.enabled && 'opacity-50 pointer-events-none')}>
                <div className="space-y-2">
                  <Label htmlFor="discussion-max-rounds">{t('settings.discussion.maxRounds')}</Label>
                  <Input
                    id="discussion-max-rounds"
                    type="number"
                    min={1}
                    max={5}
                    value={formData.discussion.maxRounds}
                    onChange={(e) => handleDiscussionChange('maxRounds', parseInt(e.target.value) || 3)}
                  />
                  <p className="text-xs text-muted-foreground">{t('settings.discussion.maxRoundsHint')}</p>
                </div>

                {enabledProviders.length > 0 && (
                  <ModelSelectorRow
                    label={t('settings.discussion.model')}
                    providerId={formData.discussion.providerId}
                    model={formData.discussion.model}
                    providers={enabledProviders}
                    onProviderChange={(id) => handleDiscussionChange('providerId', id)}
                    onModelChange={(m) => handleDiscussionChange('model', m)}
                    t={t}
                  />
                )}
              </div>
            </div>

            {/* Prediction Config */}
            <Separator className="my-4" />
            <div className="space-y-4">
              <div>
                <h4 className="text-sm font-medium">{t('settings.prediction.title')}</h4>
                <p className="text-sm text-muted-foreground">{t('settings.prediction.description')}</p>
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('settings.prediction.enabled')}</Label>
                  <p className="text-sm text-muted-foreground">{t('settings.prediction.enabledDescription')}</p>
                </div>
                <ToggleSwitch
                  checked={formData.prediction.enabled}
                  onCheckedChange={(checked) => handlePredictionChange('enabled', checked)}
                />
              </div>

              <div className={cn('space-y-4 transition-opacity', !formData.prediction.enabled && 'opacity-50 pointer-events-none')}>
                {enabledProviders.length > 0 && (
                  <ModelSelectorRow
                    label={t('settings.prediction.model')}
                    providerId={formData.prediction.providerId}
                    model={formData.prediction.model}
                    providers={enabledProviders}
                    onProviderChange={(id) => handlePredictionChange('providerId', id)}
                    onModelChange={(m) => handlePredictionChange('model', m)}
                    t={t}
                  />
                )}

                <Separator />

                {/* Auto Retrain */}
                <div className="space-y-3">
                  <h5 className="text-sm font-medium">{t('settings.autoRetrain')}</h5>

                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>{t('settings.autoRetrainEnabled')}</Label>
                      <p className="text-sm text-muted-foreground">{t('settings.autoRetrainDesc')}</p>
                    </div>
                    <ToggleSwitch
                      checked={formData.prediction.autoRetrainEnabled ?? false}
                      onCheckedChange={(checked) => handlePredictionChange('autoRetrainEnabled', checked)}
                    />
                  </div>

                  <div className={cn('space-y-2 transition-opacity', !formData.prediction.autoRetrainEnabled && 'opacity-50 pointer-events-none')}>
                    <Label htmlFor="auto-retrain-interval">{t('settings.autoRetrainInterval')}</Label>
                    <Input
                      id="auto-retrain-interval"
                      type="number"
                      min={1}
                      max={30}
                      step={1}
                      value={formData.prediction.autoRetrainIntervalDays ?? 7}
                      onChange={(e) => handlePredictionChange('autoRetrainIntervalDays', Math.min(30, Math.max(1, parseInt(e.target.value) || 7)))}
                    />
                  </div>
                </div>

                <Separator />

                {/* Auto Tune */}
                <div className="space-y-3">
                  <h5 className="text-sm font-medium">{t('settings.autoTune')}</h5>

                  <div className="flex items-center justify-between">
                    <div className="space-y-0.5">
                      <Label>{t('settings.autoTuneEnabled')}</Label>
                      <p className="text-sm text-muted-foreground">{t('settings.autoTuneDesc')}</p>
                    </div>
                    <ToggleSwitch
                      checked={formData.prediction.autoTuneEnabled ?? false}
                      onCheckedChange={(checked) => handlePredictionChange('autoTuneEnabled', checked)}
                    />
                  </div>

                  <div className={cn('space-y-4 transition-opacity', !formData.prediction.autoTuneEnabled && 'opacity-50 pointer-events-none')}>
                    <div className="space-y-2">
                      <Label htmlFor="auto-tune-interval">{t('settings.autoTuneInterval')}</Label>
                      <Input
                        id="auto-tune-interval"
                        type="number"
                        min={7}
                        max={90}
                        step={1}
                        value={formData.prediction.autoTuneIntervalDays ?? 30}
                        onChange={(e) => handlePredictionChange('autoTuneIntervalDays', Math.min(90, Math.max(7, parseInt(e.target.value) || 30)))}
                      />
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="auto-tune-max-iter">{t('settings.autoTuneMaxIterations')}</Label>
                      <Input
                        id="auto-tune-max-iter"
                        type="number"
                        min={1}
                        max={10}
                        step={1}
                        value={formData.prediction.autoTuneMaxIterations ?? 3}
                        onChange={(e) => handlePredictionChange('autoTuneMaxIterations', Math.min(10, Math.max(1, parseInt(e.target.value) || 3)))}
                      />
                    </div>
                  </div>
                </div>
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
    </TooltipProvider>
  )
}
