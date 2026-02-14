import { useTranslation } from 'react-i18next'
import { AlertTriangle } from 'lucide-react'

import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import type { LlmProvider, ModelAssignmentsConfig, ModelAssignment } from '@/types'

type Purpose = 'analysis' | 'synthesis' | 'chat' | 'embedding'

const PURPOSES: Purpose[] = ['analysis', 'synthesis', 'chat', 'embedding']

const PURPOSE_LABEL_KEYS: Record<Purpose, string> = {
  analysis: 'settings.models.analysisModel',
  synthesis: 'settings.models.synthesisModel',
  chat: 'settings.models.chatModel',
  embedding: 'settings.models.embeddingModel',
}

interface ModelAssignmentsProps {
  providers: LlmProvider[]
  assignments: ModelAssignmentsConfig
  onAssignmentsChange: (assignments: ModelAssignmentsConfig) => void
  advancedSettings: {
    maxClarificationRounds: number
    clarificationConfidenceThreshold: number
  }
  onAdvancedChange: (key: string, value: number) => void
}

export function ModelAssignments({
  providers,
  assignments,
  onAssignmentsChange,
  advancedSettings,
  onAdvancedChange,
}: ModelAssignmentsProps) {
  const { t } = useTranslation('admin')

  const enabledProviders = providers.filter((p) => p.isEnabled)

  const getProviderById = (id: string | null): LlmProvider | undefined => {
    if (!id) return undefined
    return enabledProviders.find((p) => p.id === id)
  }

  const handleAssignmentChange = (
    purpose: Purpose,
    field: keyof ModelAssignment,
    value: string | null
  ) => {
    const current = assignments[purpose]
    let updated: ModelAssignment

    if (field === 'providerId') {
      // When provider changes, reset model to first available or empty
      const newProvider = enabledProviders.find((p) => p.id === value)
      const firstModel = newProvider?.models[0] ?? ''
      updated = { providerId: value, model: firstModel }
    } else {
      updated = { ...current, [field]: value ?? '' }
    }

    onAssignmentsChange({
      ...assignments,
      [purpose]: updated,
    })
  }

  // Check if embedding is assigned to an Anthropic provider
  const embeddingProvider = getProviderById(assignments.embedding.providerId)
  const showEmbeddingWarning = embeddingProvider?.providerType === 'anthropic'

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h4 className="text-sm font-medium">{t('settings.models.title')}</h4>
        <p className="text-sm text-muted-foreground">{t('settings.models.description')}</p>
      </div>

      {enabledProviders.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">
          {t('settings.models.noProviders')}
        </p>
      ) : (
        <div className="space-y-4">
          {PURPOSES.map((purpose) => {
            const assignment = assignments[purpose]
            const selectedProvider = getProviderById(assignment.providerId)
            const availableModels = selectedProvider?.models ?? []

            return (
              <div key={purpose} className="grid gap-4 sm:grid-cols-[140px_1fr_1fr] items-center">
                <Label className="text-sm">
                  {t(PURPOSE_LABEL_KEYS[purpose] as never)}
                </Label>

                {/* Provider dropdown */}
                <select
                  value={assignment.providerId ?? ''}
                  onChange={(e) =>
                    handleAssignmentChange(
                      purpose,
                      'providerId',
                      e.target.value || null
                    )
                  }
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
                >
                  <option value="">{t('settings.models.selectProvider')}</option>
                  {enabledProviders.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name}
                    </option>
                  ))}
                </select>

                {/* Model dropdown */}
                <select
                  value={assignment.model}
                  onChange={(e) =>
                    handleAssignmentChange(purpose, 'model', e.target.value)
                  }
                  disabled={!assignment.providerId || availableModels.length === 0}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <option value="">{t('settings.models.selectModel')}</option>
                  {availableModels.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                </select>
              </div>
            )
          })}
        </div>
      )}

      {/* Embedding Anthropic warning */}
      {showEmbeddingWarning && (
        <div className="bg-amber-500/10 border border-amber-500/20 rounded-lg p-3 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-600 dark:text-amber-400 mt-0.5 shrink-0" />
          <p className="text-sm text-amber-600 dark:text-amber-400">
            {t('settings.models.embeddingAnthropicWarning')}
          </p>
        </div>
      )}

      <Separator />

      {/* Advanced Settings */}
      <div className="space-y-1">
        <h4 className="text-sm font-medium">{t('settings.models.advancedTitle')}</h4>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="model-max-rounds">{t('settings.maxClarificationRounds')}</Label>
          <Input
            id="model-max-rounds"
            type="number"
            min={0}
            max={5}
            value={advancedSettings.maxClarificationRounds}
            onChange={(e) =>
              onAdvancedChange('maxClarificationRounds', parseInt(e.target.value) || 0)
            }
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="model-confidence">{t('settings.clarificationThreshold')}</Label>
          <Input
            id="model-confidence"
            type="number"
            min={0}
            max={1}
            step={0.1}
            value={advancedSettings.clarificationConfidenceThreshold}
            onChange={(e) =>
              onAdvancedChange(
                'clarificationConfidenceThreshold',
                parseFloat(e.target.value) || 0.6
              )
            }
          />
          <p className="text-xs text-muted-foreground">
            {t('settings.clarificationThresholdHint')}
          </p>
        </div>
      </div>
    </div>
  )
}
