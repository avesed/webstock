import { useState, useEffect, useCallback } from 'react'
import { useMutation } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Eye, EyeOff, X, Save, RotateCcw, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import { cn } from '@/lib/utils'
import type { LlmProvider, LlmProviderType } from '@/types'

interface LlmProvidersProps {
  providers: LlmProvider[]
  onRefresh: () => void
}

interface ProviderFormData {
  name: string
  providerType: LlmProviderType
  apiKey: string
  baseUrl: string
  models: string[]
}

const EMPTY_FORM: ProviderFormData = {
  name: '',
  providerType: 'openai',
  apiKey: '',
  baseUrl: '',
  models: [],
}

export function LlmProviders({ providers, onRefresh }: LlmProvidersProps) {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const { toast } = useToast()

  // State
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(null)
  const [formData, setFormData] = useState<ProviderFormData>(EMPTY_FORM)
  const [showApiKey, setShowApiKey] = useState(false)
  const [newModelInput, setNewModelInput] = useState('')
  const [isCreating, setIsCreating] = useState(false)

  // Derive selected provider from props
  const selectedProvider = selectedProviderId
    ? providers.find((p) => p.id === selectedProviderId) ?? null
    : null

  // Sync form data when selection changes
  const syncFormFromProvider = useCallback((provider: LlmProvider | null) => {
    if (provider) {
      setFormData({
        name: provider.name,
        providerType: provider.providerType,
        apiKey: provider.apiKeySet ? '***' : '',
        baseUrl: provider.baseUrl ?? '',
        models: [...provider.models],
      })
    } else {
      setFormData(EMPTY_FORM)
    }
    setShowApiKey(false)
    setNewModelInput('')
  }, [])

  // When providers list changes, re-sync selected provider form data
  useEffect(() => {
    if (isCreating) return
    if (selectedProviderId) {
      const provider = providers.find((p) => p.id === selectedProviderId)
      if (provider) {
        syncFormFromProvider(provider)
      } else {
        // Provider was deleted or no longer exists
        setSelectedProviderId(null)
        setFormData(EMPTY_FORM)
      }
    }
  }, [providers, selectedProviderId, isCreating, syncFormFromProvider])

  // Mutations
  const createMutation = useMutation({
    mutationFn: adminApi.createLlmProvider,
    onSuccess: (newProvider) => {
      toast({ title: t('settings.provider.created') })
      setIsCreating(false)
      onRefresh()
      setSelectedProviderId(newProvider.id)
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: Parameters<typeof adminApi.updateLlmProvider>[1] }) =>
      adminApi.updateLlmProvider(id, data),
    onSuccess: () => {
      toast({ title: t('settings.provider.updated') })
      onRefresh()
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: adminApi.deleteLlmProvider,
    onSuccess: () => {
      toast({ title: t('settings.provider.deleted') })
      setSelectedProviderId(null)
      setFormData(EMPTY_FORM)
      onRefresh()
    },
    onError: () => {
      toast({ title: t('settings.provider.deleteInUse'), variant: 'destructive' })
    },
  })

  const isPending = createMutation.isPending || updateMutation.isPending || deleteMutation.isPending

  // Handlers
  const handleSelectProvider = (id: string) => {
    if (id === '') {
      setSelectedProviderId(null)
      setFormData(EMPTY_FORM)
      setIsCreating(false)
      return
    }
    setIsCreating(false)
    setSelectedProviderId(id)
    const provider = providers.find((p) => p.id === id)
    if (provider) {
      syncFormFromProvider(provider)
    }
  }

  const handleCreateNew = () => {
    setSelectedProviderId(null)
    setIsCreating(true)
    setFormData(EMPTY_FORM)
    setShowApiKey(false)
    setNewModelInput('')
  }

  const handleDelete = () => {
    if (!selectedProviderId) return
    const confirmed = window.confirm(t('settings.provider.confirmDelete'))
    if (confirmed) {
      deleteMutation.mutate(selectedProviderId)
    }
  }

  const handleAddModel = () => {
    const model = newModelInput.trim()
    if (model && !formData.models.includes(model)) {
      setFormData((prev) => ({
        ...prev,
        models: [...prev.models, model],
      }))
      setNewModelInput('')
    }
  }

  const handleRemoveModel = (model: string) => {
    setFormData((prev) => ({
      ...prev,
      models: prev.models.filter((m) => m !== model),
    }))
  }

  const handleModelInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleAddModel()
    }
  }

  const handleSave = () => {
    if (isCreating) {
      createMutation.mutate({
        name: formData.name,
        providerType: formData.providerType,
        apiKey: formData.apiKey || null,
        baseUrl: formData.baseUrl || null,
        models: formData.models,
      })
    } else if (selectedProviderId) {
      const updateData: Parameters<typeof adminApi.updateLlmProvider>[1] = {
        name: formData.name,
        baseUrl: formData.baseUrl || null,
        models: formData.models,
      }

      // Only include apiKey if it was changed (not the masked placeholder)
      if (formData.apiKey !== '***') {
        updateData.apiKey = formData.apiKey || null
      }

      updateMutation.mutate({ id: selectedProviderId, data: updateData })
    }
  }

  const handleReset = () => {
    if (isCreating) {
      setFormData(EMPTY_FORM)
    } else if (selectedProvider) {
      syncFormFromProvider(selectedProvider)
    }
    setNewModelInput('')
  }

  const hasSelection = isCreating || selectedProviderId !== null

  return (
    <div className="space-y-4">
      {/* Top row: selector, create, delete */}
      <div className="flex items-center gap-2">
        <select
          value={isCreating ? '' : (selectedProviderId ?? '')}
          onChange={(e) => handleSelectProvider(e.target.value)}
          disabled={isPending}
          className="flex-1 h-10 px-3 rounded-md border border-input bg-background text-sm"
        >
          <option value="">{t('settings.provider.selectProvider')}</option>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name} ({provider.providerType})
            </option>
          ))}
        </select>

        <Button
          variant="outline"
          size="icon"
          onClick={handleCreateNew}
          disabled={isPending}
          title={t('settings.provider.newProvider')}
        >
          <Plus className="h-4 w-4" />
        </Button>

        <Button
          variant="outline"
          size="icon"
          onClick={handleDelete}
          disabled={isPending || !selectedProviderId || isCreating}
          title={t('settings.provider.deleteProvider')}
          className={cn(
            selectedProviderId && !isCreating && 'text-destructive hover:text-destructive hover:bg-destructive/10'
          )}
        >
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>

      {/* Form */}
      {hasSelection && (
        <div className="space-y-4">
          {/* Name */}
          <div className="space-y-2">
            <Label htmlFor="provider-name">{t('settings.provider.name')}</Label>
            <Input
              id="provider-name"
              value={formData.name}
              onChange={(e) => setFormData((prev) => ({ ...prev, name: e.target.value }))}
              disabled={isPending}
            />
          </div>

          {/* Provider Type */}
          <div className="space-y-2">
            <Label htmlFor="provider-type">{t('settings.provider.type')}</Label>
            <select
              id="provider-type"
              value={formData.providerType}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, providerType: e.target.value as LlmProviderType }))
              }
              disabled={isPending || !isCreating}
              className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
            >
              <option value="openai">{t('settings.provider.typeOpenai')}</option>
              <option value="anthropic">{t('settings.provider.typeAnthropic')}</option>
            </select>
          </div>

          {/* API Key */}
          <div className="space-y-2">
            <Label htmlFor="provider-api-key">{t('settings.provider.apiKey')}</Label>
            <div className="relative">
              <Input
                id="provider-api-key"
                type={showApiKey ? 'text' : 'password'}
                value={formData.apiKey === '***' ? '' : formData.apiKey}
                onChange={(e) => setFormData((prev) => ({ ...prev, apiKey: e.target.value }))}
                placeholder={
                  !isCreating && selectedProvider?.apiKeySet
                    ? t('settings.provider.apiKey') + ' (already set)'
                    : undefined
                }
                disabled={isPending}
              />
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6"
                onClick={() => setShowApiKey(!showApiKey)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>

          {/* Base URL */}
          <div className="space-y-2">
            <Label htmlFor="provider-base-url">{t('settings.provider.baseUrl')}</Label>
            <Input
              id="provider-base-url"
              value={formData.baseUrl}
              onChange={(e) => setFormData((prev) => ({ ...prev, baseUrl: e.target.value }))}
              placeholder={
                formData.providerType === 'openai'
                  ? 'https://api.openai.com/v1'
                  : 'https://api.anthropic.com'
              }
              disabled={isPending}
            />
            <p className="text-xs text-muted-foreground">{t('settings.provider.baseUrlHint')}</p>
          </div>

          {/* Models */}
          <div className="space-y-2">
            <Label>{t('settings.provider.models')}</Label>

            {/* Model chips */}
            {formData.models.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {formData.models.map((model) => (
                  <span
                    key={model}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-secondary text-secondary-foreground text-xs font-medium"
                  >
                    {model}
                    <button
                      type="button"
                      onClick={() => handleRemoveModel(model)}
                      disabled={isPending}
                      className="inline-flex items-center justify-center h-4 w-4 rounded-full hover:bg-muted-foreground/20 transition-colors"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}

            {/* Add model input */}
            <div className="flex items-center gap-2">
              <Input
                value={newModelInput}
                onChange={(e) => setNewModelInput(e.target.value)}
                onKeyDown={handleModelInputKeyDown}
                placeholder={t('settings.provider.modelPlaceholder')}
                disabled={isPending}
                className="flex-1"
              />
              <Button
                variant="outline"
                size="sm"
                onClick={handleAddModel}
                disabled={isPending || !newModelInput.trim()}
              >
                {t('settings.provider.addModel')}
              </Button>
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={handleReset}
              disabled={isPending}
            >
              <RotateCcw className="mr-2 h-4 w-4" />
              {tCommon('actions.reset')}
            </Button>
            <Button
              onClick={handleSave}
              disabled={isPending || !formData.name.trim()}
            >
              {isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              {tCommon('actions.save')}
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
