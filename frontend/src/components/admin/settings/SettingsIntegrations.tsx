import { useState, useEffect, useCallback, useMemo, type ReactNode } from 'react'
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
  Database,
  ExternalLink,
  Activity,
  HelpCircle,
} from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { useToast, useFormatters } from '@/hooks'
import { adminApi } from '@/api/admin'
import type {
  IntegrationConfigUpdate,
  StockPulseConfigUpdate,
  AlphaForgeConfigUpdate,
  StockPulseHealth,
  StockPulseMarket,
  StockPulseProvider,
} from '@/api/admin'
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

      {/* StockPulse Integration */}
      <StockPulseCard />

      {/* AlphaForge Integration */}
      <AlphaForgeCard />

      {/* Export News */}
      <ExportNewsCard />
    </div>
  )
}

interface StockPulseFormState {
  stockpulse_url: string
  stockpulse_api_key: string
}

function StockPulseCard() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { formatRelativeTime } = useFormatters()

  const [formData, setFormData] = useState<StockPulseFormState>({
    stockpulse_url: '',
    stockpulse_api_key: '',
  })
  const [hasChanges, setHasChanges] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['admin-stockpulse-config'],
    queryFn: adminApi.getStockPulseConfig,
  })

  const healthEnabled = !!config?.stockpulse_url
  const { data: health, isLoading: healthLoading } = useQuery({
    queryKey: ['admin-stockpulse-health'],
    queryFn: adminApi.getStockPulseHealth,
    refetchInterval: 30_000,
    enabled: healthEnabled,
  })

  useEffect(() => {
    if (config) {
      setFormData({
        stockpulse_url: config.stockpulse_url,
        stockpulse_api_key: '',
      })
      setHasChanges(false)
    }
  }, [config])

  const updateMutation = useMutation({
    mutationFn: adminApi.updateStockPulseConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-stockpulse-config'] })
      queryClient.invalidateQueries({ queryKey: ['admin-stockpulse-health'] })
      toast({ title: t('settings.integrations.stockpulse.saved') })
      // Clear API key field — never display the saved key
      setFormData((prev) => ({ ...prev, stockpulse_api_key: '' }))
      setHasChanges(false)
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const testMutation = useMutation({
    mutationFn: adminApi.testStockPulse,
    onSuccess: (result) => {
      if (result.connected) {
        const latency = result.latency_ms
        toast({
          title:
            typeof latency === 'number'
              ? t('settings.integrations.stockpulse.connectedWithLatency', { latency })
              : t('settings.integrations.stockpulse.connected'),
        })
      } else {
        toast({
          title: t('settings.integrations.stockpulse.connectionFailed'),
          description: result.error,
          variant: 'destructive',
        })
      }
    },
    onError: () => {
      toast({
        title: t('settings.integrations.stockpulse.connectionFailed'),
        variant: 'destructive',
      })
    },
  })

  const handleChange = useCallback(
    (field: keyof StockPulseFormState, value: string) => {
      setFormData((prev) => ({ ...prev, [field]: value }))
      setHasChanges(true)
    },
    []
  )

  const handleSave = () => {
    const payload: StockPulseConfigUpdate = {
      stockpulse_url: formData.stockpulse_url,
    }
    // Only send API key if user typed a new value
    if (formData.stockpulse_api_key) {
      payload.stockpulse_api_key = formData.stockpulse_api_key
    }
    updateMutation.mutate(payload)
  }

  if (configLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-32">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-5 w-5" />
          {t('settings.integrations.stockpulse.title')}
        </CardTitle>
        <CardDescription>
          {t('settings.integrations.stockpulse.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* URL */}
        <div className="space-y-2">
          <Label htmlFor="stockpulse-url">
            {t('settings.integrations.stockpulse.url')}
          </Label>
          <Input
            id="stockpulse-url"
            type="url"
            placeholder={t('settings.integrations.stockpulse.urlPlaceholder')}
            value={formData.stockpulse_url}
            onChange={(e) => handleChange('stockpulse_url', e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            {t('settings.integrations.stockpulse.urlHelp')}
          </p>
        </div>

        <Separator />

        {/* API Key */}
        <div className="space-y-2">
          <Label htmlFor="stockpulse-api-key">
            {t('settings.integrations.stockpulse.apiKey')}
          </Label>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Input
                id="stockpulse-api-key"
                type={showApiKey ? 'text' : 'password'}
                placeholder={
                  config?.stockpulse_api_key_set
                    ? t('settings.integrations.stockpulse.apiKeyMasked')
                    : t('settings.integrations.stockpulse.apiKeyPlaceholder')
                }
                value={formData.stockpulse_api_key}
                onChange={(e) => handleChange('stockpulse_api_key', e.target.value)}
              />
              <button
                type="button"
                aria-label={
                  showApiKey
                    ? t('settings.integrations.stockpulse.hideApiKey')
                    : t('settings.integrations.stockpulse.showApiKey')
                }
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowApiKey((v) => !v)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {config?.stockpulse_api_key_set ? (
              <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-3 w-3" />
                {t('settings.integrations.stockpulse.apiKeySet')}
              </span>
            ) : (
              <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                <XCircle className="h-3 w-3" />
                {t('settings.integrations.stockpulse.apiKeyNotSet')}
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('settings.integrations.stockpulse.apiKeyHelp')}
          </p>
        </div>

        <Separator />

        {/* Action Buttons */}
        <div className="flex flex-wrap items-center gap-3">
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
              ? t('settings.integrations.stockpulse.saving')
              : t('settings.integrations.stockpulse.save')}
          </Button>
          <Button
            variant="outline"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending || !healthEnabled}
          >
            {testMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plug className="mr-2 h-4 w-4" />
            )}
            {testMutation.isPending
              ? t('settings.integrations.stockpulse.testing')
              : t('settings.integrations.stockpulse.test')}
          </Button>
          {testMutation.isSuccess && (
            <span className="flex items-center gap-1 text-sm">
              {testMutation.data.connected ? (
                <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-4 w-4" />
                  {t('settings.integrations.stockpulse.connected')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-destructive">
                  <XCircle className="h-4 w-4" />
                  {t('settings.integrations.stockpulse.connectionFailed')}
                </span>
              )}
            </span>
          )}
        </div>

        {/* Not configured notice */}
        {!healthEnabled && (
          <Alert>
            <HelpCircle className="h-4 w-4" />
            <AlertTitle>{t('settings.integrations.stockpulse.notConfigured')}</AlertTitle>
            <AlertDescription>
              {t('settings.integrations.stockpulse.notConfiguredHelp')}
            </AlertDescription>
          </Alert>
        )}

        {/* Service Health Overview */}
        {healthEnabled && (
          <>
            <Separator />
            <StockPulseHealthOverview
              health={health}
              isLoading={healthLoading}
              formatRelativeTime={formatRelativeTime}
            />
          </>
        )}

        {/* External Console Link */}
        {healthEnabled && config?.stockpulse_url && (
          <>
            <Separator />
            <div>
              <a
                href={config.stockpulse_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
              >
                <ExternalLink className="h-4 w-4" />
                {t('settings.integrations.stockpulse.openConsole')}
              </a>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

interface StockPulseHealthOverviewProps {
  readonly health: StockPulseHealth | undefined
  readonly isLoading: boolean
  readonly formatRelativeTime: (date: Date | string | number) => string
}

function StockPulseHealthOverview({
  health,
  isLoading,
  formatRelativeTime,
}: StockPulseHealthOverviewProps) {
  const { t } = useTranslation('admin')

  if (isLoading && !health) {
    return (
      <div className="flex items-center justify-center h-24">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!health) {
    return null
  }

  let statusInfo: { label: string; className: string; icon: ReactNode }
  if (!health.connected) {
    statusInfo = {
      label: t('settings.integrations.stockpulse.statusDisconnected'),
      className: 'bg-destructive/10 text-destructive border-destructive/30',
      icon: <XCircle className="h-4 w-4" />,
    }
  } else if (health.status === 'healthy') {
    statusInfo = {
      label: t('settings.integrations.stockpulse.statusHealthy'),
      className:
        'bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400',
      icon: <CheckCircle2 className="h-4 w-4" />,
    }
  } else if (health.status === 'degraded') {
    statusInfo = {
      label: t('settings.integrations.stockpulse.statusDegraded'),
      className:
        'bg-amber-500/10 text-amber-700 border-amber-500/30 dark:text-amber-400',
      icon: <AlertTriangle className="h-4 w-4" />,
    }
  } else {
    statusInfo = {
      label: t('settings.integrations.stockpulse.statusUnknown'),
      className: 'bg-muted text-muted-foreground border-border',
      icon: <HelpCircle className="h-4 w-4" />,
    }
  }

  return (
    <div className="space-y-5">
      {/* Service Status header */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">
            {t('settings.integrations.stockpulse.serviceStatus')}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <div
            className={`inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-semibold ${statusInfo.className}`}
          >
            {statusInfo.icon}
            {statusInfo.label}
          </div>
          {/* Connection error */}
          {!health.connected && health.error && (
            <span className="text-xs text-destructive">{health.error}</span>
          )}
        </div>
        {health.connected && (
          <div className="flex flex-wrap gap-2">
            <ServiceIndicatorBadge
              label={t('settings.integrations.stockpulse.redis')}
              status={health.redis}
            />
            <ServiceIndicatorBadge
              label={t('settings.integrations.stockpulse.database')}
              status={health.database}
            />
            <ServiceIndicatorBadge
              label={t('settings.integrations.stockpulse.executor')}
              status={health.executor}
            />
          </div>
        )}
      </div>

      {/* Providers */}
      {health.connected && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Plug className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">
              {t('settings.integrations.stockpulse.providers')}
            </span>
          </div>
          {health.providers.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t('settings.integrations.stockpulse.noProviders')}
            </p>
          ) : (
            <div className="space-y-1.5 rounded-md border divide-y">
              {health.providers.map((provider) => (
                <ProviderRow
                  key={provider.name}
                  provider={provider}
                  formatRelativeTime={formatRelativeTime}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* Markets */}
      {health.connected && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">
              {t('settings.integrations.stockpulse.markets')}
            </span>
          </div>
          {health.markets.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              {t('settings.integrations.stockpulse.noMarkets')}
            </p>
          ) : (
            <div className="overflow-x-auto rounded-md border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50 text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">
                      {t('settings.integrations.stockpulse.market')}
                    </th>
                    <th className="px-3 py-2 text-left font-medium">
                      {t('settings.integrations.stockpulse.lastCollection')}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t('settings.integrations.stockpulse.totalBars')}
                    </th>
                    <th className="px-3 py-2 text-right font-medium">
                      {t('settings.integrations.stockpulse.totalSymbols')}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {health.markets.map((market) => (
                    <MarketRow
                      key={market.market}
                      market={market}
                      formatRelativeTime={formatRelativeTime}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ServiceIndicatorBadge({
  label,
  status,
}: {
  readonly label: string
  readonly status: string
}) {
  const normalized = status?.toLowerCase() ?? 'unknown'
  let variant: 'default' | 'secondary' | 'destructive' | 'outline' = 'outline'
  let className = ''
  if (normalized === 'ok' || normalized === 'healthy') {
    className =
      'bg-green-500/10 text-green-700 border-green-500/30 dark:text-green-400'
  } else if (normalized === 'down' || normalized === 'degraded' || normalized === 'error') {
    variant = 'destructive'
  } else {
    variant = 'secondary'
  }
  return (
    <Badge variant={variant} className={className}>
      <span className="mr-1.5 font-medium">{label}:</span>
      <span className="capitalize">{status || '—'}</span>
    </Badge>
  )
}

function ProviderRow({
  provider,
  formatRelativeTime,
}: {
  readonly provider: StockPulseProvider
  readonly formatRelativeTime: (date: Date | string | number) => string
}) {
  const { t } = useTranslation('admin')
  const status = provider.healthStatus?.toLowerCase() ?? 'unknown'

  let StatusIcon = HelpCircle
  let statusColor = 'text-muted-foreground'
  let statusLabel = t('settings.integrations.stockpulse.statusUnknown')

  if (status === 'healthy') {
    StatusIcon = CheckCircle2
    statusColor = 'text-green-600 dark:text-green-400'
    statusLabel = t('settings.integrations.stockpulse.statusHealthy')
  } else if (status === 'degraded') {
    StatusIcon = AlertTriangle
    statusColor = 'text-amber-600 dark:text-amber-400'
    statusLabel = t('settings.integrations.stockpulse.statusDegraded')
  } else if (status === 'down' || status === 'error') {
    StatusIcon = XCircle
    statusColor = 'text-destructive'
    statusLabel = t('settings.integrations.stockpulse.statusDisconnected')
  }

  return (
    <div className="flex flex-wrap items-center gap-3 px-3 py-2">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        {provider.enabled ? (
          <Wifi className="h-3.5 w-3.5 text-primary" />
        ) : (
          <WifiOff className="h-3.5 w-3.5 text-muted-foreground" />
        )}
        <span className="text-sm font-medium truncate">{provider.name}</span>
      </div>
      <span className={`inline-flex items-center gap-1 text-xs ${statusColor}`}>
        <StatusIcon className="h-3.5 w-3.5" />
        {statusLabel}
      </span>
      <span className="text-xs text-muted-foreground min-w-[5rem] text-right">
        {provider.lastCheck
          ? `${t('settings.integrations.stockpulse.healthCheck')}: ${formatRelativeTime(provider.lastCheck)}`
          : t('settings.integrations.stockpulse.neverChecked')}
      </span>
      {provider.errorMessage && (
        <span className="w-full text-xs text-destructive truncate">
          {provider.errorMessage}
        </span>
      )}
    </div>
  )
}

function MarketRow({
  market,
  formatRelativeTime,
}: {
  readonly market: StockPulseMarket
  readonly formatRelativeTime: (date: Date | string | number) => string
}) {
  const { t } = useTranslation('admin')

  const collectionStaleness = useMemo(() => {
    if (!market.lastCollectionAt) {
      return { className: 'text-muted-foreground', text: t('settings.integrations.stockpulse.neverChecked') }
    }
    const ts = new Date(market.lastCollectionAt).getTime()
    if (Number.isNaN(ts)) {
      return { className: 'text-muted-foreground', text: '—' }
    }
    const ageMs = Date.now() - ts
    const text = formatRelativeTime(market.lastCollectionAt)
    if (ageMs > 24 * 60 * 60 * 1000) {
      return { className: 'text-destructive font-medium', text }
    }
    if (ageMs > 60 * 60 * 1000) {
      return { className: 'text-amber-600 dark:text-amber-400 font-medium', text }
    }
    return { className: 'text-foreground', text }
  }, [market.lastCollectionAt, formatRelativeTime, t])

  return (
    <tr className="border-t">
      <td className="px-3 py-2 font-medium uppercase">{market.market}</td>
      <td className={`px-3 py-2 text-xs ${collectionStaleness.className}`}>
        {collectionStaleness.text}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {market.totalBars.toLocaleString()}
      </td>
      <td className="px-3 py-2 text-right tabular-nums">
        {market.totalSymbols.toLocaleString()}
      </td>
    </tr>
  )
}

interface AlphaForgeFormState {
  alphaforge_url: string
  alphaforge_api_key: string
}

function AlphaForgeCard() {
  const { t } = useTranslation('admin')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [formData, setFormData] = useState<AlphaForgeFormState>({
    alphaforge_url: '',
    alphaforge_api_key: '',
  })
  const [hasChanges, setHasChanges] = useState(false)
  const [showApiKey, setShowApiKey] = useState(false)

  const { data: config, isLoading: configLoading } = useQuery({
    queryKey: ['admin-alphaforge-config'],
    queryFn: adminApi.getAlphaForgeConfig,
  })

  useEffect(() => {
    if (config) {
      setFormData({
        alphaforge_url: config.alphaforge_url,
        alphaforge_api_key: '',
      })
      setHasChanges(false)
    }
  }, [config])

  const updateMutation = useMutation({
    mutationFn: adminApi.updateAlphaForgeConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-alphaforge-config'] })
      toast({ title: t('settings.integrations.alphaforge.saved') })
      // Clear API key field — never display the saved key
      setFormData((prev) => ({ ...prev, alphaforge_api_key: '' }))
      setHasChanges(false)
    },
    onError: () => {
      toast({ title: tCommon('status.error'), variant: 'destructive' })
    },
  })

  const testMutation = useMutation({
    mutationFn: adminApi.testAlphaForge,
    onSuccess: (result) => {
      if (result.connected) {
        const latency = result.latency_ms
        toast({
          title:
            typeof latency === 'number'
              ? t('settings.integrations.alphaforge.connectedWithLatency', { latency })
              : t('settings.integrations.alphaforge.connected'),
        })
      } else {
        toast({
          title: t('settings.integrations.alphaforge.connectionFailed'),
          description: result.error,
          variant: 'destructive',
        })
      }
    },
    onError: () => {
      toast({
        title: t('settings.integrations.alphaforge.connectionFailed'),
        variant: 'destructive',
      })
    },
  })

  const handleChange = useCallback(
    (field: keyof AlphaForgeFormState, value: string) => {
      setFormData((prev) => ({ ...prev, [field]: value }))
      setHasChanges(true)
    },
    []
  )

  const handleSave = () => {
    const payload: AlphaForgeConfigUpdate = {
      alphaforge_url: formData.alphaforge_url,
    }
    // Only send API key if user typed a new value
    if (formData.alphaforge_api_key) {
      payload.alphaforge_api_key = formData.alphaforge_api_key
    }
    updateMutation.mutate(payload)
  }

  const configuredEnabled = !!config?.alphaforge_url

  if (configLoading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center h-32">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="h-5 w-5" />
          {t('settings.integrations.alphaforge.title')}
        </CardTitle>
        <CardDescription>
          {t('settings.integrations.alphaforge.description')}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* URL */}
        <div className="space-y-2">
          <Label htmlFor="alphaforge-url">
            {t('settings.integrations.alphaforge.url')}
          </Label>
          <Input
            id="alphaforge-url"
            type="url"
            placeholder={t('settings.integrations.alphaforge.urlPlaceholder')}
            value={formData.alphaforge_url}
            onChange={(e) => handleChange('alphaforge_url', e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            {t('settings.integrations.alphaforge.urlHelp')}
          </p>
        </div>

        <Separator />

        {/* API Key */}
        <div className="space-y-2">
          <Label htmlFor="alphaforge-api-key">
            {t('settings.integrations.alphaforge.apiKey')}
          </Label>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Input
                id="alphaforge-api-key"
                type={showApiKey ? 'text' : 'password'}
                placeholder={
                  config?.alphaforge_api_key_set
                    ? t('settings.integrations.alphaforge.apiKeyMasked')
                    : t('settings.integrations.alphaforge.apiKeyPlaceholder')
                }
                value={formData.alphaforge_api_key}
                onChange={(e) => handleChange('alphaforge_api_key', e.target.value)}
              />
              <button
                type="button"
                aria-label={
                  showApiKey
                    ? t('settings.integrations.alphaforge.hideApiKey')
                    : t('settings.integrations.alphaforge.showApiKey')
                }
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                onClick={() => setShowApiKey((v) => !v)}
              >
                {showApiKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {config?.alphaforge_api_key_set ? (
              <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                <CheckCircle2 className="h-3 w-3" />
                {t('settings.integrations.alphaforge.apiKeySet')}
              </span>
            ) : (
              <span className="flex items-center gap-1 text-amber-600 dark:text-amber-400">
                <XCircle className="h-3 w-3" />
                {t('settings.integrations.alphaforge.apiKeyNotSet')}
              </span>
            )}
          </p>
          <p className="text-xs text-muted-foreground">
            {t('settings.integrations.alphaforge.apiKeyHelp')}
          </p>
        </div>

        <Separator />

        {/* Action Buttons */}
        <div className="flex flex-wrap items-center gap-3">
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
              ? t('settings.integrations.alphaforge.saving')
              : t('settings.integrations.alphaforge.save')}
          </Button>
          <Button
            variant="outline"
            onClick={() => testMutation.mutate()}
            disabled={testMutation.isPending || !configuredEnabled}
          >
            {testMutation.isPending ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plug className="mr-2 h-4 w-4" />
            )}
            {testMutation.isPending
              ? t('settings.integrations.alphaforge.testing')
              : t('settings.integrations.alphaforge.test')}
          </Button>
          {testMutation.isSuccess && (
            <span className="flex items-center gap-1 text-sm">
              {testMutation.data.connected ? (
                <span className="flex items-center gap-1 text-green-600 dark:text-green-400">
                  <CheckCircle2 className="h-4 w-4" />
                  {t('settings.integrations.alphaforge.connected')}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-destructive">
                  <XCircle className="h-4 w-4" />
                  {t('settings.integrations.alphaforge.connectionFailed')}
                </span>
              )}
            </span>
          )}
        </div>

        {/* Not configured notice */}
        {!configuredEnabled && (
          <Alert>
            <HelpCircle className="h-4 w-4" />
            <AlertTitle>{t('settings.integrations.alphaforge.notConfigured')}</AlertTitle>
            <AlertDescription>
              {t('settings.integrations.alphaforge.notConfiguredHelp')}
            </AlertDescription>
          </Alert>
        )}

        {/* External Console Link */}
        {configuredEnabled && config?.alphaforge_url && (
          <>
            <Separator />
            <div>
              <a
                href={config.alphaforge_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm font-medium text-primary hover:underline"
              >
                <ExternalLink className="h-4 w-4" />
                {t('settings.integrations.alphaforge.openConsole')}
              </a>
            </div>
          </>
        )}
      </CardContent>
    </Card>
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
