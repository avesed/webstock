import { useState, useEffect, useCallback } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import {
  Loader2,
  Save,
  Plug,
  CheckCircle2,
  XCircle,
  BarChart3,
  Eye,
  EyeOff,
  Download,
  AlertTriangle,
  ArrowLeftRight,
  Wifi,
  WifiOff,
} from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useToast } from '@/hooks'
import { adminApi } from '@/api/admin'
import type { IntegrationConfigUpdate } from '@/api/admin'
import { ToggleSwitch } from './shared'

interface FormState {
  newsforge_url: string
  newsforge_api_key: string
  newsforge_push_enabled: boolean
  newsforge_webhook_secret: string
  newsforge_proxy_enabled: boolean
}

export default function SettingsIntegrations() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [formData, setFormData] = useState<FormState>({
    newsforge_url: '',
    newsforge_api_key: '',
    newsforge_push_enabled: false,
    newsforge_webhook_secret: '',
    newsforge_proxy_enabled: false,
  })
  const [hasChanges, setHasChanges] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)
  const [showWebhookSecret, setShowWebhookSecret] = useState(false)
  const [showProxyConfirm, setShowProxyConfirm] = useState(false)

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['admin-integration-config'],
    queryFn: adminApi.getIntegrationConfig,
  })

  const { data: stats, isLoading: statsLoading, isError: statsError } = useQuery({
    queryKey: ['admin-integration-stats'],
    queryFn: adminApi.getIntegrationStats,
  })

  useEffect(() => {
    if (config) {
      setFormData({
        newsforge_url: config.newsforge_url,
        newsforge_api_key: '',
        newsforge_push_enabled: config.newsforge_push_enabled,
        newsforge_webhook_secret: '',
        newsforge_proxy_enabled: config.newsforge_proxy_enabled,
      })
      setHasChanges(false)
    }
  }, [config])

  const updateMutation = useMutation({
    mutationFn: adminApi.updateIntegrationConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-integration-config'] })
      queryClient.invalidateQueries({ queryKey: ['admin-integration-stats'] })
      toast({ title: t('settings.integrations.newsforge.saved') })
      setHasChanges(false)
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const testMutation = useMutation({
    mutationFn: adminApi.testIntegration,
    onSuccess: (result) => {
      if (result.connected) {
        toast({ title: t('settings.integrations.newsforge.connected') })
      } else {
        toast({
          title: t('settings.integrations.newsforge.connectionFailed'),
          description: result.error,
          variant: 'destructive',
        })
      }
    },
    onError: () => {
      toast({
        title: t('settings.integrations.newsforge.connectionFailed'),
        variant: 'destructive',
      })
    },
  })

  const handleChange = useCallback(
    (field: keyof FormState, value: string | boolean) => {
      setFormData((prev) => ({ ...prev, [field]: value }))
      setHasChanges(true)
    },
    []
  )

  const handleProxyToggle = useCallback(
    (checked: boolean) => {
      if (checked) {
        // Show confirmation when enabling proxy mode
        setShowProxyConfirm(true)
      } else {
        handleChange('newsforge_proxy_enabled', false)
      }
    },
    [handleChange]
  )

  const confirmProxyEnable = useCallback(() => {
    handleChange('newsforge_proxy_enabled', true)
    setShowProxyConfirm(false)
  }, [handleChange])

  const handleSave = () => {
    const payload: IntegrationConfigUpdate = {
      newsforge_url: formData.newsforge_url,
      newsforge_push_enabled: formData.newsforge_push_enabled,
      newsforge_proxy_enabled: formData.newsforge_proxy_enabled,
    }
    // Only send secrets if user typed a new value
    if (formData.newsforge_api_key) {
      payload.newsforge_api_key = formData.newsforge_api_key
    }
    if (formData.newsforge_webhook_secret) {
      payload.newsforge_webhook_secret = formData.newsforge_webhook_secret
    }
    updateMutation.mutate(payload)
  }

  const formatTimestamp = useCallback(
    (ts: string | null) => {
      if (!ts) return t('settings.integrations.newsforge.never')
      return new Date(ts).toLocaleString()
    },
    [t]
  )

  if (configLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Proxy Mode */}
      <Card className={formData.newsforge_proxy_enabled
        ? 'border-primary/50 bg-primary/5 dark:bg-primary/10'
        : ''
      }>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ArrowLeftRight className="h-5 w-5" />
            {t('settings.integrations.newsforge.proxyMode')}
          </CardTitle>
          <CardDescription>
            {t('settings.integrations.newsforge.proxyModeDescription')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base font-medium">
                {t('settings.integrations.newsforge.proxyMode')}
              </Label>
              <p className="text-sm text-muted-foreground">
                {t('settings.integrations.newsforge.proxyModeHint')}
              </p>
            </div>
            <ToggleSwitch
              checked={formData.newsforge_proxy_enabled}
              onCheckedChange={handleProxyToggle}
            />
          </div>

          {/* Confirmation warning when enabling */}
          {showProxyConfirm && (
            <Alert variant="destructive" className="border-amber-500/50 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950/50 dark:text-amber-200 [&>svg]:text-amber-600">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>{t('settings.integrations.newsforge.proxyConfirmTitle')}</AlertTitle>
              <AlertDescription className="mt-2">
                <p className="mb-3">{t('settings.integrations.newsforge.proxyConfirmMessage')}</p>
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={confirmProxyEnable}
                  >
                    {t('settings.integrations.newsforge.proxyConfirmEnable')}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setShowProxyConfirm(false)}
                  >
                    {tCommon('actions.cancel')}
                  </Button>
                </div>
              </AlertDescription>
            </Alert>
          )}

          {/* Status indicator */}
          <div className="flex items-center gap-2 rounded-lg border px-4 py-3">
            {formData.newsforge_proxy_enabled ? (
              <>
                <Wifi className="h-4 w-4 text-primary" />
                <span className="text-sm font-medium text-primary">
                  {t('settings.integrations.newsforge.proxyStatusOn')}
                </span>
                {testMutation.isSuccess && testMutation.data.connected && (
                  <span className="ml-auto flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-3 w-3" />
                    {t('settings.integrations.newsforge.connected')}
                  </span>
                )}
              </>
            ) : (
              <>
                <WifiOff className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm text-muted-foreground">
                  {t('settings.integrations.newsforge.proxyStatusOff')}
                </span>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Connection Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Plug className="h-5 w-5" />
            {t('settings.integrations.newsforge.title')}
          </CardTitle>
          <CardDescription>
            {t('settings.integrations.newsforge.description')}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* NewsForge URL */}
          <div className="space-y-2">
            <Label htmlFor="newsforge-url">
              {t('settings.integrations.newsforge.url')}
            </Label>
            <Input
              id="newsforge-url"
              type="url"
              placeholder={t('settings.integrations.newsforge.urlPlaceholder')}
              value={formData.newsforge_url}
              onChange={(e) => handleChange('newsforge_url', e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.integrations.newsforge.urlHint')}
            </p>
          </div>

          <Separator />

          {/* API Key */}
          <div className="space-y-2">
            <Label htmlFor="newsforge-api-key">
              {t('settings.integrations.newsforge.apiKey')}
            </Label>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Input
                  id="newsforge-api-key"
                  type={showApiKey ? 'text' : 'password'}
                  placeholder={t('settings.integrations.newsforge.apiKeyPlaceholder')}
                  value={formData.newsforge_api_key}
                  onChange={(e) => handleChange('newsforge_api_key', e.target.value)}
                />
                <button
                  type="button"
                  aria-label={showApiKey ? t('settings.integrations.newsforge.hideApiKey') : t('settings.integrations.newsforge.showApiKey')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowApiKey((v) => !v)}
                >
                  {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {config?.newsforge_api_key_set ? (
                <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-3 w-3" />
                  {t('settings.integrations.newsforge.apiKeySet')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                  <XCircle className="h-3 w-3" />
                  {t('settings.integrations.newsforge.apiKeyNotSet')}
                </span>
              )}
            </p>
          </div>

          {/* Webhook Secret */}
          <div className="space-y-2">
            <Label htmlFor="newsforge-webhook-secret">
              {t('settings.integrations.newsforge.webhookSecret')}
            </Label>
            <div className="flex items-center gap-2">
              <div className="relative flex-1">
                <Input
                  id="newsforge-webhook-secret"
                  type={showWebhookSecret ? 'text' : 'password'}
                  placeholder={t('settings.integrations.newsforge.webhookSecretPlaceholder')}
                  value={formData.newsforge_webhook_secret}
                  onChange={(e) => handleChange('newsforge_webhook_secret', e.target.value)}
                />
                <button
                  type="button"
                  aria-label={showWebhookSecret ? t('settings.integrations.newsforge.hideWebhookSecret') : t('settings.integrations.newsforge.showWebhookSecret')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                  onClick={() => setShowWebhookSecret((v) => !v)}
                >
                  {showWebhookSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">
              {config?.newsforge_webhook_secret_set ? (
                <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-3 w-3" />
                  {t('settings.integrations.newsforge.webhookSecretSet')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                  <XCircle className="h-3 w-3" />
                  {t('settings.integrations.newsforge.webhookSecretNotSet')}
                </span>
              )}
            </p>
          </div>

          <Separator />

          {/* Enable Push Toggle */}
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label>{t('settings.integrations.newsforge.pushEnabled')}</Label>
              <p className="text-xs text-muted-foreground">
                {t('settings.integrations.newsforge.pushEnabledDescription')}
              </p>
            </div>
            <ToggleSwitch
              checked={formData.newsforge_push_enabled}
              onCheckedChange={(v) => handleChange('newsforge_push_enabled', v)}
            />
          </div>

          <Separator />

          {/* Action Buttons */}
          <div className="flex items-center gap-3">
            <Button
              onClick={handleSave}
              disabled={!hasChanges || updateMutation.isPending}
            >
              {updateMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Save className="mr-2 h-4 w-4" />
              )}
              {updateMutation.isPending
                ? t('settings.integrations.newsforge.saving')
                : t('settings.integrations.newsforge.save')}
            </Button>
            <Button
              variant="outline"
              onClick={() => testMutation.mutate()}
              disabled={testMutation.isPending}
            >
              {testMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Plug className="mr-2 h-4 w-4" />
              )}
              {testMutation.isPending
                ? t('settings.integrations.newsforge.testing')
                : t('settings.integrations.newsforge.testConnection')}
            </Button>
            {testMutation.isSuccess && (
              <span className="flex items-center gap-1 text-sm">
                {testMutation.data.connected ? (
                  <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                    <CheckCircle2 className="h-4 w-4" />
                    {t('settings.integrations.newsforge.connected')}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-destructive">
                    <XCircle className="h-4 w-4" />
                    {t('settings.integrations.newsforge.connectionFailed')}
                  </span>
                )}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Statistics */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="h-5 w-5" />
            {t('settings.integrations.newsforge.statsTitle')}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {statsLoading ? (
            <div className="flex items-center justify-center h-24">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : statsError ? (
            <div className="flex items-center justify-center h-24 text-sm text-destructive">
              <XCircle className="mr-2 h-4 w-4" />
              {tCommon('status.error')}
            </div>
          ) : stats ? (
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
              <StatItem
                label={t('settings.integrations.newsforge.totalPushed')}
                value={stats.total_pushed.toLocaleString()}
              />
              <StatItem
                label={t('settings.integrations.newsforge.totalDuplicates')}
                value={stats.total_duplicates.toLocaleString()}
              />
              <StatItem
                label={t('settings.integrations.newsforge.totalErrors')}
                value={stats.total_errors.toLocaleString()}
                variant={stats.total_errors > 0 ? 'destructive' : 'default'}
              />
              <StatItem
                label={t('settings.integrations.newsforge.lastPushAt')}
                value={formatTimestamp(stats.last_push_at)}
              />
              <StatItem
                label={t('settings.integrations.newsforge.lastSyncAt')}
                value={formatTimestamp(stats.last_sync_at)}
              />
            </div>
          ) : null}
        </CardContent>
      </Card>

      {/* Export News */}
      <ExportNewsCard />
    </div>
  )
}

function ExportNewsCard() {
  const { t } = useTranslation('admin')
  const { toast } = useToast()
  const [sinceDate, setSinceDate] = useState('')
  const [market, setMarket] = useState('')

  const exportMutation = useMutation({
    mutationFn: async () => {
      const params: { since?: string; market?: string } = {}
      if (sinceDate) params.since = new Date(sinceDate).toISOString()
      if (market) params.market = market
      await adminApi.exportNews(params)
    },
    onSuccess: () => {
      toast({ title: t('settings.integrations.export.success') })
    },
    onError: () => {
      toast({
        title: t('settings.integrations.export.error'),
        variant: 'destructive',
      })
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Download className="h-5 w-5" />
          {t('settings.integrations.export.title')}
        </CardTitle>
        <CardDescription>
          {t('settings.integrations.export.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="export-since">
              {t('settings.integrations.export.since')}
            </Label>
            <Input
              id="export-since"
              type="date"
              value={sinceDate}
              onChange={(e) => setSinceDate(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.integrations.export.sinceHint')}
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="export-market">
              {t('settings.integrations.export.market')}
            </Label>
            <Input
              id="export-market"
              type="text"
              placeholder={t('settings.integrations.export.marketPlaceholder')}
              value={market}
              onChange={(e) => setMarket(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              {t('settings.integrations.export.marketHint')}
            </p>
          </div>
        </div>

        <Button
          onClick={() => exportMutation.mutate()}
          disabled={exportMutation.isPending}
        >
          {exportMutation.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <Download className="mr-2 h-4 w-4" />
          )}
          {exportMutation.isPending
            ? t('settings.integrations.export.exporting')
            : t('settings.integrations.export.exportButton')}
        </Button>
      </CardContent>
    </Card>
  )
}

function StatItem({
  label,
  value,
  variant = 'default',
}: {
  readonly label: string
  readonly value: string
  readonly variant?: 'default' | 'destructive'
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={
          variant === 'destructive'
            ? 'text-lg font-semibold text-destructive'
            : 'text-lg font-semibold'
        }
      >
        {value}
      </p>
    </div>
  )
}
