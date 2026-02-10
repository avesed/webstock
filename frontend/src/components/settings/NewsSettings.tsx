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
import { Eye, EyeOff, Newspaper, Database, Clock } from 'lucide-react'

export type NewsContentSource = 'scraper' | 'polygon'

export interface NewsContentSettings {
  source: NewsContentSource
  polygonApiKey: string | null
  retentionDays: number
}

interface NewsSettingsProps {
  settings: NewsContentSettings
  onUpdate: (settings: Partial<NewsContentSettings>) => void
  isLoading?: boolean
}

const DEFAULT_SETTINGS: NewsContentSettings = {
  source: 'scraper',
  polygonApiKey: null,
  retentionDays: 30,
}

export default function NewsSettings({ settings, onUpdate, isLoading = false }: NewsSettingsProps) {
  const { t } = useTranslation('settings')

  // Local state for inputs that save on blur
  const [polygonKeyDraft, setPolygonKeyDraft] = useState(settings.polygonApiKey || '')
  const [showPolygonKey, setShowPolygonKey] = useState(false)
  const [retentionDays, setRetentionDays] = useState(settings.retentionDays || DEFAULT_SETTINGS.retentionDays)

  // Refs to track if value changed
  const polygonKeySnapshot = useRef(settings.polygonApiKey)
  const retentionSnapshot = useRef(settings.retentionDays)

  // Sync when settings change from server
  useEffect(() => {
    setPolygonKeyDraft(settings.polygonApiKey || '')
    polygonKeySnapshot.current = settings.polygonApiKey
    setRetentionDays(settings.retentionDays || DEFAULT_SETTINGS.retentionDays)
    retentionSnapshot.current = settings.retentionDays
  }, [settings.polygonApiKey, settings.retentionDays])

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
