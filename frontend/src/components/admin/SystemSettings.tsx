import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2, Eye, EyeOff, RotateCcw, Save, Info } from 'lucide-react'

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
import type { SystemConfig } from '@/types'

const DEFAULT_CONFIG: SystemConfig = {
  llm: {
    apiKey: null,
    baseUrl: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    maxTokens: 4096,
    temperature: 0.7,
  },
  news: {
    defaultSource: 'scraper',
    retentionDays: 30,
    embeddingModel: 'text-embedding-3-small',
    filterModel: 'gpt-4o-mini',
    autoFetchEnabled: true,
  },
  features: {
    allowUserApiKeys: true,
    allowUserCustomModels: false,
    enableNewsAnalysis: true,
    enableStockAnalysis: true,
  },
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
  const [showApiKey, setShowApiKey] = useState(false)
  const [formData, setFormData] = useState<SystemConfig>(DEFAULT_CONFIG)
  const [hasChanges, setHasChanges] = useState(false)

  // Query
  const { data: config, isLoading, error } = useQuery({
    queryKey: ['admin-system-config'],
    queryFn: adminApi.getSystemConfig,
  })

  // Sync form data with fetched config
  useEffect(() => {
    if (config) {
      setFormData(config)
      setHasChanges(false)
    }
  }, [config])

  // Mutation
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
    setHasChanges(true)
  }

  const handleSave = () => {
    updateMutation.mutate(formData)
  }

  const handleReset = () => {
    if (config) {
      setFormData(config)
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
        {/* LLM Configuration */}
        <Card>
          <CardHeader>
            <CardTitle>{t('settings.llmTitle')}</CardTitle>
            <CardDescription>{t('settings.llmDescription')}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="llm-api-key">{t('settings.apiKey')}</Label>
              <div className="relative">
                <Input
                  id="llm-api-key"
                  type={showApiKey ? 'text' : 'password'}
                  value={formData.llm.apiKey || ''}
                  onChange={(e) => handleChange('llm', 'apiKey', e.target.value || null)}
                  placeholder={t('settings.apiKeyPlaceholder')}
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

            <div className="space-y-2">
              <Label htmlFor="llm-base-url">{t('settings.baseUrl')}</Label>
              <Input
                id="llm-base-url"
                value={formData.llm.baseUrl}
                onChange={(e) => handleChange('llm', 'baseUrl', e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label htmlFor="llm-model">{t('settings.model')}</Label>
                <Input
                  id="llm-model"
                  value={formData.llm.model}
                  onChange={(e) => handleChange('llm', 'model', e.target.value)}
                  placeholder="gpt-4o-mini"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="llm-max-tokens">{t('settings.maxTokens')}</Label>
                <Input
                  id="llm-max-tokens"
                  type="number"
                  min={1}
                  max={128000}
                  value={formData.llm.maxTokens}
                  onChange={(e) => handleChange('llm', 'maxTokens', parseInt(e.target.value) || 4096)}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="llm-temperature">{t('settings.temperature')}</Label>
                <Input
                  id="llm-temperature"
                  type="number"
                  min={0}
                  max={2}
                  step={0.1}
                  value={formData.llm.temperature}
                  onChange={(e) => handleChange('llm', 'temperature', parseFloat(e.target.value) || 0.7)}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        {/* News Processing Configuration */}
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
          </CardContent>
        </Card>

        {/* Feature Toggles */}
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
          </CardContent>
        </Card>

        {/* Action Buttons */}
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
