import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Eye, EyeOff, Newspaper, Database, Brain, Clock } from 'lucide-react'

export type NewsContentSource = 'scraper' | 'polygon'

export interface NewsContentSettings {
  source: NewsContentSource
  polygonApiKey: string | null
  retentionDays: number
  // Custom AI API settings (for self-hosted LLMs or other OpenAI-compatible providers)
  openaiBaseUrl: string | null
  openaiApiKey: string | null
  embeddingModel: string
  filterModel: string
}

interface NewsSettingsProps {
  settings: NewsContentSettings
  onUpdate: (settings: Partial<NewsContentSettings>) => void
  isLoading?: boolean
}

// Description key type for type-safe translations
type ModelDescriptionKey = 'more accurate' | 'legacy' | 'smarter' | 'cheapest'

interface ModelOption {
  value: string
  label: string
  recommended?: boolean
  descriptionKey?: ModelDescriptionKey
}

const EMBEDDING_MODELS: ModelOption[] = [
  { value: 'text-embedding-3-small', label: 'text-embedding-3-small', recommended: true },
  { value: 'text-embedding-3-large', label: 'text-embedding-3-large', descriptionKey: 'more accurate' },
  { value: 'text-embedding-ada-002', label: 'text-embedding-ada-002', descriptionKey: 'legacy' },
]

const FILTER_MODELS: ModelOption[] = [
  { value: 'gpt-4o-mini', label: 'gpt-4o-mini', recommended: true },
  { value: 'gpt-4o', label: 'gpt-4o', descriptionKey: 'smarter' },
  { value: 'gpt-3.5-turbo', label: 'gpt-3.5-turbo', descriptionKey: 'cheapest' },
]

const DEFAULT_SETTINGS: NewsContentSettings = {
  source: 'scraper',
  polygonApiKey: null,
  retentionDays: 30,
  openaiBaseUrl: null,
  openaiApiKey: null,
  embeddingModel: 'text-embedding-3-small',
  filterModel: 'gpt-4o-mini',
}

export default function NewsSettings({ settings, onUpdate, isLoading = false }: NewsSettingsProps) {
  const { t } = useTranslation('settings')

  // Local state for inputs that save on blur
  const [polygonKeyDraft, setPolygonKeyDraft] = useState(settings.polygonApiKey || '')
  const [showPolygonKey, setShowPolygonKey] = useState(false)
  const [retentionDays, setRetentionDays] = useState(settings.retentionDays || DEFAULT_SETTINGS.retentionDays)

  // Custom AI API settings state
  const [openaiBaseUrlDraft, setOpenaiBaseUrlDraft] = useState(settings.openaiBaseUrl || '')
  const [openaiApiKeyDraft, setOpenaiApiKeyDraft] = useState(settings.openaiApiKey || '')
  const [showOpenaiApiKey, setShowOpenaiApiKey] = useState(false)
  const [embeddingModelDraft, setEmbeddingModelDraft] = useState(settings.embeddingModel || DEFAULT_SETTINGS.embeddingModel)
  const [filterModelDraft, setFilterModelDraft] = useState(settings.filterModel || DEFAULT_SETTINGS.filterModel)

  // Refs to track if value changed
  const polygonKeySnapshot = useRef(settings.polygonApiKey)
  const retentionSnapshot = useRef(settings.retentionDays)
  const openaiBaseUrlSnapshot = useRef(settings.openaiBaseUrl)
  const openaiApiKeySnapshot = useRef(settings.openaiApiKey)
  const embeddingModelSnapshot = useRef(settings.embeddingModel)
  const filterModelSnapshot = useRef(settings.filterModel)

  // Sync when settings change from server
  useEffect(() => {
    setPolygonKeyDraft(settings.polygonApiKey || '')
    polygonKeySnapshot.current = settings.polygonApiKey
    setRetentionDays(settings.retentionDays || DEFAULT_SETTINGS.retentionDays)
    retentionSnapshot.current = settings.retentionDays
    setOpenaiBaseUrlDraft(settings.openaiBaseUrl || '')
    openaiBaseUrlSnapshot.current = settings.openaiBaseUrl
    setOpenaiApiKeyDraft(settings.openaiApiKey || '')
    openaiApiKeySnapshot.current = settings.openaiApiKey
    setEmbeddingModelDraft(settings.embeddingModel || DEFAULT_SETTINGS.embeddingModel)
    embeddingModelSnapshot.current = settings.embeddingModel
    setFilterModelDraft(settings.filterModel || DEFAULT_SETTINGS.filterModel)
    filterModelSnapshot.current = settings.filterModel
  }, [settings.polygonApiKey, settings.retentionDays, settings.openaiBaseUrl, settings.openaiApiKey, settings.embeddingModel, settings.filterModel])

  const handleSourceChange = (source: NewsContentSource) => {
    onUpdate({ source })
  }

  const handlePolygonKeyBlur = () => {
    const newValue = polygonKeyDraft || null
    if (newValue === polygonKeySnapshot.current) return
    polygonKeySnapshot.current = newValue
    onUpdate({ polygonApiKey: newValue })
  }

  const handleRetentionCommit = (value: number[]) => {
    const newValue = value[0]
    if (newValue === undefined || newValue === retentionSnapshot.current) return
    retentionSnapshot.current = newValue
    onUpdate({ retentionDays: newValue })
  }

  const handleOpenaiBaseUrlBlur = () => {
    const newValue = openaiBaseUrlDraft || null
    if (newValue === openaiBaseUrlSnapshot.current) return
    openaiBaseUrlSnapshot.current = newValue
    onUpdate({ openaiBaseUrl: newValue })
  }

  const handleOpenaiApiKeyBlur = () => {
    const newValue = openaiApiKeyDraft || null
    if (newValue === openaiApiKeySnapshot.current) return
    openaiApiKeySnapshot.current = newValue
    onUpdate({ openaiApiKey: newValue })
  }

  const handleEmbeddingModelChange = (model: string) => {
    setEmbeddingModelDraft(model)
  }

  const handleEmbeddingModelBlur = () => {
    const newValue = embeddingModelDraft || DEFAULT_SETTINGS.embeddingModel
    if (newValue === embeddingModelSnapshot.current) return
    embeddingModelSnapshot.current = newValue
    onUpdate({ embeddingModel: newValue })
  }

  const handleFilterModelChange = (model: string) => {
    setFilterModelDraft(model)
  }

  const handleFilterModelBlur = () => {
    const newValue = filterModelDraft || DEFAULT_SETTINGS.filterModel
    if (newValue === filterModelSnapshot.current) return
    filterModelSnapshot.current = newValue
    onUpdate({ filterModel: newValue })
  }

  // Check if using custom model (not in predefined list)
  const isCustomEmbeddingModel = !EMBEDDING_MODELS.find(m => m.value === embeddingModelDraft)
  const isCustomFilterModel = !FILTER_MODELS.find(m => m.value === filterModelDraft)

  const currentSettings = { ...DEFAULT_SETTINGS, ...settings }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Newspaper className="h-5 w-5" />
          {t('newsContent.title')}
        </CardTitle>
        <CardDescription>
          {t('newsContent.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Data Source Selection */}
        <div className="space-y-2">
          <Label className="flex items-center gap-2">
            <Database className="h-4 w-4" />
            {t('newsContent.source.label')}
          </Label>
          <p className="text-sm text-muted-foreground">
            {t('newsContent.source.description')}
          </p>
          <Select
            value={currentSettings.source}
            onValueChange={handleSourceChange}
            disabled={isLoading}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="scraper">
                {t('newsContent.source.scraper')}
              </SelectItem>
              <SelectItem value="polygon">
                {t('newsContent.source.polygon')}
              </SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Polygon API Key - only shown when polygon source selected */}
        {currentSettings.source === 'polygon' && (
          <div className="space-y-2 pl-4 border-l-2 border-primary/20">
            <Label htmlFor="polygonApiKey">{t('newsContent.polygonKey.label')}</Label>
            <p className="text-sm text-muted-foreground">
              {t('newsContent.polygonKey.description')}
            </p>
            <div className="relative">
              <Input
                id="polygonApiKey"
                type={showPolygonKey ? 'text' : 'password'}
                value={polygonKeyDraft}
                onChange={(e) => setPolygonKeyDraft(e.target.value)}
                onBlur={handlePolygonKeyBlur}
                placeholder={t('newsContent.polygonKey.placeholder')}
                disabled={isLoading}
              />
              <Button
                variant="ghost"
                size="icon"
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6"
                onClick={() => setShowPolygonKey(!showPolygonKey)}
                aria-label={showPolygonKey ? t('newsContent.polygonKey.hide') : t('newsContent.polygonKey.show')}
              >
                {showPolygonKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              {t('newsContent.polygonKey.getKey')}{' '}
              <a
                href="https://polygon.io/"
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline"
              >
                polygon.io
              </a>
            </p>
          </div>
        )}

        <div className="border-t pt-6">
          {/* Retention Days Slider */}
          <div className="space-y-4">
            <Label className="flex items-center gap-2">
              <Clock className="h-4 w-4" />
              {t('newsContent.retention.label')}
            </Label>
            <p className="text-sm text-muted-foreground">
              {t('newsContent.retention.description')}
            </p>
            <div className="flex items-center gap-4">
              <Slider
                value={[retentionDays]}
                onValueChange={(value) => {
                  const v = value[0]
                  if (v !== undefined) setRetentionDays(v)
                }}
                onValueCommit={handleRetentionCommit}
                min={7}
                max={365}
                step={1}
                className="flex-1"
                disabled={isLoading}
              />
              <span className="min-w-[80px] text-sm font-medium text-right">
                {retentionDays} {t('newsContent.retention.days')}
              </span>
            </div>
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>7 {t('newsContent.retention.days')}</span>
              <span>365 {t('newsContent.retention.days')}</span>
            </div>
          </div>
        </div>

        <div className="border-t pt-6">
          {/* AI Model Selection */}
          <div className="space-y-4">
            <Label className="flex items-center gap-2">
              <Brain className="h-4 w-4" />
              {t('newsContent.models.title')}
            </Label>
            <p className="text-sm text-muted-foreground">
              {t('newsContent.models.description')}
            </p>

            {/* Custom API Configuration */}
            <div className="space-y-4 p-4 bg-muted/30 rounded-lg border border-dashed">
              <p className="text-sm font-medium text-muted-foreground">
                {t('newsContent.customApi.title')}
              </p>

              {/* Custom Base URL */}
              <div className="space-y-2">
                <Label htmlFor="openaiBaseUrl" className="text-sm">
                  {t('newsContent.customApi.baseUrl')}
                </Label>
                <Input
                  id="openaiBaseUrl"
                  type="url"
                  value={openaiBaseUrlDraft}
                  onChange={(e) => setOpenaiBaseUrlDraft(e.target.value)}
                  onBlur={handleOpenaiBaseUrlBlur}
                  placeholder={t('newsContent.customApi.baseUrlPlaceholder')}
                  disabled={isLoading}
                />
                <p className="text-xs text-muted-foreground">
                  {t('newsContent.customApi.baseUrlHint')}
                </p>
              </div>

              {/* Custom API Key */}
              <div className="space-y-2">
                <Label htmlFor="openaiApiKey" className="text-sm">
                  {t('newsContent.customApi.apiKey')}
                </Label>
                <div className="relative">
                  <Input
                    id="openaiApiKey"
                    type={showOpenaiApiKey ? 'text' : 'password'}
                    value={openaiApiKeyDraft}
                    onChange={(e) => setOpenaiApiKeyDraft(e.target.value)}
                    onBlur={handleOpenaiApiKeyBlur}
                    placeholder={t('newsContent.customApi.apiKeyPlaceholder')}
                    disabled={isLoading}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    type="button"
                    className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6"
                    onClick={() => setShowOpenaiApiKey(!showOpenaiApiKey)}
                    aria-label={showOpenaiApiKey ? t('newsContent.polygonKey.hide') : t('newsContent.polygonKey.show')}
                  >
                    {showOpenaiApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t('newsContent.customApi.apiKeyHint')}
                </p>
              </div>
            </div>

            {/* Embedding Model */}
            <div className="space-y-2">
              <Label htmlFor="embeddingModel" className="text-sm">
                {t('newsContent.models.embedding')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="embeddingModel"
                  type="text"
                  value={embeddingModelDraft}
                  onChange={(e) => handleEmbeddingModelChange(e.target.value)}
                  onBlur={handleEmbeddingModelBlur}
                  placeholder="text-embedding-3-small"
                  disabled={isLoading}
                  list="embeddingModelList"
                  className="flex-1"
                />
                <datalist id="embeddingModelList">
                  {EMBEDDING_MODELS.map((model) => (
                    <option key={model.value} value={model.value} />
                  ))}
                </datalist>
              </div>
              <div className="flex flex-wrap gap-1 mt-1">
                {EMBEDDING_MODELS.map((model) => (
                  <button
                    key={model.value}
                    type="button"
                    onClick={() => {
                      handleEmbeddingModelChange(model.value)
                      onUpdate({ embeddingModel: model.value })
                    }}
                    disabled={isLoading}
                    className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${
                      embeddingModelDraft === model.value
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'bg-background hover:bg-muted border-border'
                    }`}
                  >
                    {model.label}
                    {model.recommended && ` (${t('newsContent.models.recommended')})`}
                  </button>
                ))}
                {isCustomEmbeddingModel && embeddingModelDraft && (
                  <span className="px-2 py-0.5 text-xs rounded-full bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30">
                    {t('newsContent.models.custom')}
                  </span>
                )}
              </div>
            </div>

            {/* Filter Model */}
            <div className="space-y-2">
              <Label htmlFor="filterModel" className="text-sm">
                {t('newsContent.models.filter')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="filterModel"
                  type="text"
                  value={filterModelDraft}
                  onChange={(e) => handleFilterModelChange(e.target.value)}
                  onBlur={handleFilterModelBlur}
                  placeholder="gpt-4o-mini"
                  disabled={isLoading}
                  list="filterModelList"
                  className="flex-1"
                />
                <datalist id="filterModelList">
                  {FILTER_MODELS.map((model) => (
                    <option key={model.value} value={model.value} />
                  ))}
                </datalist>
              </div>
              <div className="flex flex-wrap gap-1 mt-1">
                {FILTER_MODELS.map((model) => (
                  <button
                    key={model.value}
                    type="button"
                    onClick={() => {
                      handleFilterModelChange(model.value)
                      onUpdate({ filterModel: model.value })
                    }}
                    disabled={isLoading}
                    className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${
                      filterModelDraft === model.value
                        ? 'bg-primary text-primary-foreground border-primary'
                        : 'bg-background hover:bg-muted border-border'
                    }`}
                  >
                    {model.label}
                    {model.recommended && ` (${t('newsContent.models.recommended')})`}
                  </button>
                ))}
                {isCustomFilterModel && filterModelDraft && (
                  <span className="px-2 py-0.5 text-xs rounded-full bg-amber-500/20 text-amber-600 dark:text-amber-400 border border-amber-500/30">
                    {t('newsContent.models.custom')}
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Info Box */}
        <div className="bg-blue-500/10 border border-blue-500/20 rounded-lg p-4">
          <p className="text-sm text-blue-600 dark:text-blue-400">
            <strong>{t('newsContent.info.title')}:</strong>{' '}
            {t('newsContent.info.description')}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
