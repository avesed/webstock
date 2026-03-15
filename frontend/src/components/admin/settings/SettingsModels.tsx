import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, RotateCcw, Save } from 'lucide-react'

import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { ModelAssignments } from '../ModelAssignments'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { ToggleSwitch, DEFAULT_MODEL_ASSIGNMENTS, DEFAULT_PHASE2_CONFIG, DEFAULT_CONFIG } from './shared'
import type { SystemConfig, ModelAssignmentsConfig, Phase2Config } from '@/types'

interface FormState {
  llm: SystemConfig['llm']
  modelAssignments: ModelAssignmentsConfig
  phase2: Phase2Config
}

export default function SettingsModels() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [formData, setFormData] = useState<FormState>({
    llm: DEFAULT_CONFIG.llm,
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

  useEffect(() => {
    if (config) {
      setFormData({
        llm: config.llm,
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

  const handleModelAssignmentsChange = useCallback((assignments: ModelAssignmentsConfig) => {
    setFormData((prev) => ({ ...prev, modelAssignments: assignments }))
    setHasChanges(true)
  }, [])

  const handleLlmChange = useCallback((key: keyof SystemConfig['llm'], value: unknown) => {
    setFormData((prev) => ({ ...prev, llm: { ...prev.llm, [key]: value } }))
    setHasChanges(true)
  }, [])

  const handleCacheChange = useCallback(<K extends 'cacheEnabled' | 'cacheTtlMinutes'>(key: K, value: Phase2Config[K]) => {
    setFormData((prev) => ({
      ...prev,
      phase2: { ...prev.phase2, [key]: value },
    }))
    setHasChanges(true)
  }, [])

  const handleSave = () => {
    updateMutation.mutate({
      llm: formData.llm,
      modelAssignments: formData.modelAssignments,
      phase2: formData.phase2,
    } as SystemConfig)
  }

  const handleReset = () => {
    if (config) {
      setFormData({
        llm: config.llm,
        modelAssignments: config.modelAssignments ?? DEFAULT_MODEL_ASSIGNMENTS,
        phase2: {
          ...(formData.phase2),
          cacheEnabled: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).cacheEnabled,
          cacheTtlMinutes: (config.phase2 ?? DEFAULT_PHASE2_CONFIG).cacheTtlMinutes,
        },
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

  return (
    <div className="space-y-6">
      <ModelAssignments
        providers={providers}
        assignments={formData.modelAssignments}
        onAssignmentsChange={handleModelAssignmentsChange}
        advancedSettings={{
          maxClarificationRounds: formData.llm.maxClarificationRounds,
          clarificationConfidenceThreshold: formData.llm.clarificationConfidenceThreshold,
        }}
        onAdvancedChange={(key, value) => handleLlmChange(key as keyof SystemConfig['llm'], value)}
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
