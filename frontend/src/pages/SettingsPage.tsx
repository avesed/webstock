import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast, useLocale } from '@/hooks'
import { useAuthStore } from '@/stores/authStore'
import apiClient from '@/api/client'
import { User, Bell, Key, Moon, Sun, Eye, EyeOff, Loader2, Globe, Newspaper } from 'lucide-react'
import { useThemeStore } from '@/stores/themeStore'
import { cn } from '@/lib/utils'
import type { Locale } from '@/hooks'
import { NewsSettings } from '@/components/settings'
import type { NewsContentSettings, NewsContentSource } from '@/types'

interface ToggleButtonProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}

function ToggleButton({ checked, onCheckedChange }: ToggleButtonProps) {
  const { t } = useTranslation('common')
  return (
    <Button
      variant={checked ? "default" : "outline"}
      size="sm"
      onClick={() => onCheckedChange(!checked)}
      className="w-12 h-6 relative"
    >
      <span className={`absolute transition-all ${checked ? 'right-1' : 'left-1'}`}>
        {checked ? t('status.enabled').slice(0, 2).toUpperCase() : t('status.disabled').slice(0, 3).toUpperCase()}
      </span>
    </Button>
  )
}

interface UserSettings {
  notifications: {
    price_alerts: boolean
    news_alerts: boolean
    report_notifications: boolean
    email_notifications: boolean
  }
  api_keys: {
    finnhub_api_key: string | null
    openai_api_key: string | null
    openai_base_url: string | null
    openai_model: string | null
    openai_max_tokens: number | null
    openai_temperature: number | null
    openai_system_prompt: string | null
  }
  news_source: {
    source: string
  }
  news_content: {
    source: NewsContentSource
    polygon_api_key: string | null
    retention_days: number
    openai_base_url: string | null
    openai_api_key: string | null
    embedding_model: string
    filter_model: string
  }
}

const DEFAULT_SETTINGS: UserSettings = {
  notifications: {
    price_alerts: true,
    news_alerts: true,
    report_notifications: true,
    email_notifications: false,
  },
  api_keys: {
    finnhub_api_key: null,
    openai_api_key: null,
    openai_base_url: 'https://api.openai.com/v1',
    openai_model: null,
    openai_max_tokens: null,
    openai_temperature: null,
    openai_system_prompt: null,
  },
  news_source: {
    source: 'yfinance',
  },
  news_content: {
    source: 'scraper',
    polygon_api_key: null,
    retention_days: 30,
    openai_base_url: null,
    openai_api_key: null,
    embedding_model: 'text-embedding-3-small',
    filter_model: 'gpt-4o-mini',
  },
}

// API functions
const settingsApi = {
  get: async (): Promise<UserSettings> => {
    const response = await apiClient.get<UserSettings>('/settings')
    return response.data
  },
  update: async (settings: Partial<UserSettings>): Promise<UserSettings> => {
    const response = await apiClient.put<UserSettings>('/settings', settings)
    return response.data
  },
}

export default function SettingsPage() {
  const { t } = useTranslation('settings')
  const { t: tCommon } = useTranslation('common')
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { user } = useAuthStore()
  const { theme, setTheme } = useThemeStore()
  const { locale, setLocale } = useLocale()

  // Fetch settings from backend
  const { data: settings, isLoading: isLoadingSettings } = useQuery({
    queryKey: ['settings'],
    queryFn: settingsApi.get,
  })

  // Update settings mutation
  const updateSettingsMutation = useMutation({
    mutationFn: settingsApi.update,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      // Invalidate news queries when settings change (news source affects trending news)
      queryClient.invalidateQueries({ queryKey: ['news'] })
      queryClient.invalidateQueries({ queryKey: ['news-trending'] })
      toast({
        title: t('actions.saved'),
        description: t('actions.saved'),
      })
    },
    onError: () => {
      toast({
        title: tCommon('status.error'),
        description: tCommon('errors.serverError'),
        variant: 'destructive',
      })
    },
  })

  // Profile form state
  const [profileForm, setProfileForm] = useState({
    email: user?.email || '',
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
  })

  const [showApiKeys, setShowApiKeys] = useState({
    finnhub: false,
    openai: false,
  })

  // Use settings from backend or defaults
  const currentSettings = settings || DEFAULT_SETTINGS

  // Local state for API key inputs - saves on blur instead of every keystroke
  const [apiKeyDraft, setApiKeyDraft] = useState(currentSettings.api_keys)
  const apiKeySnapshotRef = useRef(currentSettings.api_keys)

  // Sync local draft when server data loads/changes (but not while user is editing)
  useEffect(() => {
    setApiKeyDraft(currentSettings.api_keys)
    apiKeySnapshotRef.current = currentSettings.api_keys
  }, [settings]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleApiKeyChange = (key: keyof UserSettings['api_keys'], value: string | number | null) => {
    setApiKeyDraft((prev) => ({ ...prev, [key]: value === '' ? null : value }))
  }

  const handleApiKeyBlur = (key: keyof UserSettings['api_keys']) => {
    const newValue = apiKeyDraft[key]
    const oldValue = apiKeySnapshotRef.current[key]
    if (newValue === oldValue) return // no change, skip save
    apiKeySnapshotRef.current = { ...apiKeySnapshotRef.current, [key]: newValue }
    updateSettingsMutation.mutate({
      api_keys: { ...apiKeySnapshotRef.current },
    })
  }

  // Handle numeric field changes with parsing
  const handleNumericChange = (key: 'openai_max_tokens' | 'openai_temperature', value: string) => {
    if (value === '') {
      setApiKeyDraft((prev) => ({ ...prev, [key]: null }))
      return
    }
    const num = key === 'openai_temperature' ? parseFloat(value) : parseInt(value, 10)
    if (!isNaN(num)) {
      setApiKeyDraft((prev) => ({ ...prev, [key]: num }))
    }
  }

  // Handle notification toggle
  const toggleNotification = (key: keyof UserSettings['notifications']) => {
    const newSettings = {
      notifications: {
        ...currentSettings.notifications,
        [key]: !currentSettings.notifications[key],
      },
    }
    updateSettingsMutation.mutate(newSettings)
  }

  // Handle news source update
  const updateNewsSource = (source: string) => {
    const newSettings = {
      news_source: {
        source,
      },
    }
    updateSettingsMutation.mutate(newSettings)
  }

  // Handle news content settings update
  const updateNewsContentSettings = (newsContentUpdate: Partial<NewsContentSettings>) => {
    // Map from frontend camelCase to backend snake_case
    const backendUpdate: Partial<UserSettings['news_content']> = {}
    if (newsContentUpdate.source !== undefined) {
      backendUpdate.source = newsContentUpdate.source
    }
    if (newsContentUpdate.polygonApiKey !== undefined) {
      backendUpdate.polygon_api_key = newsContentUpdate.polygonApiKey
    }
    if (newsContentUpdate.retentionDays !== undefined) {
      backendUpdate.retention_days = newsContentUpdate.retentionDays
    }
    if (newsContentUpdate.openaiBaseUrl !== undefined) {
      backendUpdate.openai_base_url = newsContentUpdate.openaiBaseUrl
    }
    if (newsContentUpdate.openaiApiKey !== undefined) {
      backendUpdate.openai_api_key = newsContentUpdate.openaiApiKey
    }
    if (newsContentUpdate.embeddingModel !== undefined) {
      backendUpdate.embedding_model = newsContentUpdate.embeddingModel
    }
    if (newsContentUpdate.filterModel !== undefined) {
      backendUpdate.filter_model = newsContentUpdate.filterModel
    }

    updateSettingsMutation.mutate({
      news_content: {
        ...currentSettings.news_content,
        ...backendUpdate,
      },
    })
  }

  // Convert backend news_content to frontend NewsContentSettings
  const newsContentSettings: NewsContentSettings = {
    source: currentSettings.news_content?.source || 'scraper',
    polygonApiKey: currentSettings.news_content?.polygon_api_key || null,
    retentionDays: currentSettings.news_content?.retention_days || 30,
    openaiBaseUrl: currentSettings.news_content?.openai_base_url || null,
    openaiApiKey: currentSettings.news_content?.openai_api_key || null,
    embeddingModel: currentSettings.news_content?.embedding_model || 'text-embedding-3-small',
    filterModel: currentSettings.news_content?.filter_model || 'gpt-4o-mini',
  }

  // Handle profile update
  const handleProfileUpdate = async () => {
    if (profileForm.newPassword && profileForm.newPassword !== profileForm.confirmPassword) {
      toast({
        title: tCommon('status.error'),
        description: tCommon('validation.passwordMismatch'),
        variant: 'destructive',
      })
      return
    }

    toast({
      title: t('profile.saved'),
      description: t('profile.saved'),
    })
  }

  const languageOptions: { value: Locale; label: string }[] = [
    { value: 'en', label: tCommon('language.en') },
    { value: 'zh', label: tCommon('language.zh') },
  ]

  if (isLoadingSettings) {
    return (
      <div className="flex h-[400px] items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">{t('title')}</h1>
        <p className="text-muted-foreground">
          {t('subtitle')}
        </p>
      </div>

      <Tabs defaultValue="profile" className="space-y-4">
        <TabsList>
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <User className="h-4 w-4" />
            {t('sections.profile')}
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" />
            {t('sections.notifications')}
          </TabsTrigger>
          <TabsTrigger value="appearance" className="flex items-center gap-2">
            {theme === 'dark' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            {t('sections.appearance')}
          </TabsTrigger>
          <TabsTrigger value="language" className="flex items-center gap-2">
            <Globe className="h-4 w-4" />
            {t('sections.language')}
          </TabsTrigger>
          <TabsTrigger value="api" className="flex items-center gap-2">
            <Key className="h-4 w-4" />
            {t('sections.ai')}
          </TabsTrigger>
          <TabsTrigger value="news-content" className="flex items-center gap-2">
            <Newspaper className="h-4 w-4" />
            {t('newsContent.title')}
          </TabsTrigger>
        </TabsList>

        {/* Profile Tab */}
        <TabsContent value="profile" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('profile.title')}</CardTitle>
              <CardDescription>
                {t('account.changePassword')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">{t('profile.email')}</Label>
                <Input
                  id="email"
                  type="email"
                  value={profileForm.email}
                  onChange={(e) => setProfileForm({ ...profileForm, email: e.target.value })}
                  placeholder={t('profile.displayNamePlaceholder')}
                />
              </div>

              <div className="border-t pt-4 mt-4">
                <h4 className="font-medium mb-4">{t('account.changePassword')}</h4>
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="currentPassword">{t('account.currentPassword')}</Label>
                    <Input
                      id="currentPassword"
                      type="password"
                      value={profileForm.currentPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, currentPassword: e.target.value })}
                      placeholder={t('account.currentPassword')}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="newPassword">{t('account.newPassword')}</Label>
                    <Input
                      id="newPassword"
                      type="password"
                      value={profileForm.newPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, newPassword: e.target.value })}
                      placeholder={t('account.newPassword')}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirmPassword">{t('account.confirmPassword')}</Label>
                    <Input
                      id="confirmPassword"
                      type="password"
                      value={profileForm.confirmPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, confirmPassword: e.target.value })}
                      placeholder={t('account.confirmPassword')}
                    />
                  </div>
                </div>
              </div>

              <Button onClick={handleProfileUpdate} className="w-full">
                {t('profile.saveChanges')}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Notifications Tab */}
        <TabsContent value="notifications" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('notifications.title')}</CardTitle>
              <CardDescription>
                {t('subtitle')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('notifications.priceAlerts')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('notifications.priceAlertsDescription')}
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.price_alerts}
                  onCheckedChange={() => toggleNotification('price_alerts')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('notifications.marketNews')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('notifications.marketNewsDescription')}
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.news_alerts}
                  onCheckedChange={() => toggleNotification('news_alerts')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('notifications.reportReady')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('notifications.reportReadyDescription')}
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.report_notifications}
                  onCheckedChange={() => toggleNotification('report_notifications')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('notifications.email')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('notifications.push')}
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.email_notifications}
                  onCheckedChange={() => toggleNotification('email_notifications')}
                />
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Appearance Tab */}
        <TabsContent value="appearance" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('appearance.title')}</CardTitle>
              <CardDescription>
                {t('appearance.theme')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>{t('appearance.theme')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('appearance.colorScheme')}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Sun className="h-4 w-4 text-muted-foreground" />
                  <ToggleButton
                    checked={theme === 'dark'}
                    onCheckedChange={(checked: boolean) => setTheme(checked ? 'dark' : 'light')}
                  />
                  <Moon className="h-4 w-4 text-muted-foreground" />
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Language Tab */}
        <TabsContent value="language" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('language.title')}</CardTitle>
              <CardDescription>
                {t('language.interfaceDescription')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label>{t('language.interface')}</Label>
                <p className="text-sm text-muted-foreground mb-3">
                  {t('language.interfaceDescription')}
                </p>
                <div className="flex gap-2">
                  {languageOptions.map((option) => (
                    <Button
                      key={option.value}
                      variant={locale === option.value ? 'default' : 'outline'}
                      onClick={() => setLocale(option.value)}
                      className={cn('flex-1')}
                    >
                      {option.label}
                    </Button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* API Keys Tab */}
        <TabsContent value="api" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>{t('ai.title')}</CardTitle>
              <CardDescription>
                {t('ai.modelDescription')}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* News Source Selection */}
              <div className="space-y-2">
                <Label htmlFor="newsSource">{tCommon('navigation.news')}</Label>
                <p className="text-sm text-muted-foreground">
                  {t('ai.languageDescription')}
                </p>
                <select
                  id="newsSource"
                  value={currentSettings.news_source?.source || 'auto'}
                  onChange={(e) => updateNewsSource(e.target.value)}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                >
                  <option value="auto">Auto (Recommended) - US: YFinance, A-shares: AKShare</option>
                  <option value="yfinance">YFinance - US stocks only</option>
                  <option value="finnhub">Finnhub - US stocks only</option>
                  <option value="akshare">AKShare - Chinese A-shares only</option>
                </select>
              </div>

              <div className="border-t pt-6">
                {/* Finnhub API Key */}
                <div className="space-y-2">
                  <Label htmlFor="finnhubKey">Finnhub API Key</Label>
                  <p className="text-sm text-muted-foreground">
                    Required for US stock market data and Finnhub news source
                  </p>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Input
                        id="finnhubKey"
                        type={showApiKeys.finnhub ? 'text' : 'password'}
                        value={apiKeyDraft.finnhub_api_key || ''}
                        onChange={(e) => handleApiKeyChange('finnhub_api_key', e.target.value)}
                        onBlur={() => handleApiKeyBlur('finnhub_api_key')}
                        placeholder="Enter your Finnhub API key"
                      />
                      <Button
                        variant="ghost"
                        size="icon"
                        className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6"
                        onClick={() => setShowApiKeys({ ...showApiKeys, finnhub: !showApiKeys.finnhub })}
                      >
                        {showApiKeys.finnhub ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Get your API key from{' '}
                    <a
                      href="https://finnhub.io/"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-primary hover:underline"
                    >
                      finnhub.io
                  </a>
                </p>
              </div>
              </div>

              <div className="border-t pt-6">
                {/* OpenAI API Key */}
                <div className="space-y-2">
                  <Label htmlFor="openaiKey">OpenAI API Key</Label>
                  <p className="text-sm text-muted-foreground">
                    Required for AI analysis and news sentiment analysis
                  </p>
                  <div className="relative">
                    <Input
                      id="openaiKey"
                      type={showApiKeys.openai ? 'text' : 'password'}
                      value={apiKeyDraft.openai_api_key || ''}
                      onChange={(e) => handleApiKeyChange('openai_api_key', e.target.value)}
                      onBlur={() => handleApiKeyBlur('openai_api_key')}
                      placeholder="Enter your OpenAI API key"
                    />
                    <Button
                      variant="ghost"
                      size="icon"
                      className="absolute right-2 top-1/2 -translate-y-1/2 h-6 w-6"
                      onClick={() => setShowApiKeys({ ...showApiKeys, openai: !showApiKeys.openai })}
                    >
                      {showApiKeys.openai ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                    </Button>
                  </div>
                </div>

                {/* OpenAI Base URL */}
                <div className="space-y-2 mt-4">
                  <Label htmlFor="openaiUrl">OpenAI Base URL</Label>
                  <p className="text-sm text-muted-foreground">
                    Optional: Custom OpenAI API base URL (for proxy or alternative providers)
                  </p>
                  <Input
                    id="openaiUrl"
                    type="text"
                    value={apiKeyDraft.openai_base_url || ''}
                    onChange={(e) => handleApiKeyChange('openai_base_url', e.target.value)}
                    onBlur={() => handleApiKeyBlur('openai_base_url')}
                    placeholder="https://api.openai.com/v1"
                  />
                </div>

                {/* OpenAI Model */}
                <div className="space-y-2 mt-4">
                  <Label htmlFor="openaiModel">{t('ai.model')}</Label>
                  <p className="text-sm text-muted-foreground">
                    {t('ai.modelDescription')}
                  </p>
                  <Input
                    id="openaiModel"
                    type="text"
                    value={apiKeyDraft.openai_model || ''}
                    onChange={(e) => handleApiKeyChange('openai_model', e.target.value)}
                    onBlur={() => handleApiKeyBlur('openai_model')}
                    placeholder="gpt-4o-mini"
                  />
                </div>

                {/* Max Tokens */}
                <div className="space-y-2 mt-4">
                  <Label htmlFor="openaiMaxTokens">Max Tokens</Label>
                  <p className="text-sm text-muted-foreground">
                    Maximum output tokens for AI responses (1-128000, leave empty for default)
                  </p>
                  <Input
                    id="openaiMaxTokens"
                    type="number"
                    min={1}
                    max={128000}
                    value={apiKeyDraft.openai_max_tokens ?? ''}
                    onChange={(e) => handleNumericChange('openai_max_tokens', e.target.value)}
                    onBlur={() => handleApiKeyBlur('openai_max_tokens')}
                    placeholder="4096"
                  />
                </div>

                {/* Temperature */}
                <div className="space-y-2 mt-4">
                  <Label htmlFor="openaiTemperature">Temperature</Label>
                  <p className="text-sm text-muted-foreground">
                    Controls randomness (0.0-2.0, leave empty for model default). Note: some models don't support custom temperature.
                  </p>
                  <Input
                    id="openaiTemperature"
                    type="number"
                    min={0}
                    max={2}
                    step={0.1}
                    value={apiKeyDraft.openai_temperature ?? ''}
                    onChange={(e) => handleNumericChange('openai_temperature', e.target.value)}
                    onBlur={() => handleApiKeyBlur('openai_temperature')}
                    placeholder="1.0"
                  />
                </div>

                {/* System Prompt */}
                <div className="space-y-2 mt-4">
                  <Label htmlFor="openaiSystemPrompt">System Prompt</Label>
                  <p className="text-sm text-muted-foreground">
                    Custom system prompt for AI chat (leave empty for default). Max 10,000 characters.
                  </p>
                  <textarea
                    id="openaiSystemPrompt"
                    className="w-full min-h-[120px] px-3 py-2 rounded-md border border-input bg-background text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                    value={apiKeyDraft.openai_system_prompt || ''}
                    onChange={(e) => handleApiKeyChange('openai_system_prompt', e.target.value)}
                    onBlur={() => handleApiKeyBlur('openai_system_prompt')}
                    placeholder="You are a knowledgeable stock market analysis assistant..."
                    maxLength={10000}
                  />
                </div>

                <p className="text-xs text-muted-foreground mt-4">
                  Get your API key from{' '}
                  <a
                    href="https://platform.openai.com/"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  >
                    platform.openai.com
                  </a>
                </p>
              </div>

              <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-4 mt-4">
                <p className="text-sm text-green-600">
                  <strong>{t('privacy.title')}:</strong> {t('privacy.dataSharingDescription')}
                </p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* News Content Tab */}
        <TabsContent value="news-content" className="space-y-4">
          <NewsSettings
            settings={newsContentSettings}
            onUpdate={updateNewsContentSettings}
            isLoading={updateSettingsMutation.isPending}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
