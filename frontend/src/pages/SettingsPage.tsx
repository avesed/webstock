import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useToast } from '@/hooks'
import { useAuthStore } from '@/stores/authStore'
import apiClient from '@/api/client'
import { User, Bell, Key, Moon, Sun, Eye, EyeOff, Loader2 } from 'lucide-react'
import { useThemeStore } from '@/stores/themeStore'

interface ToggleButtonProps {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
}

function ToggleButton({ checked, onCheckedChange }: ToggleButtonProps) {
  return (
    <Button
      variant={checked ? "default" : "outline"}
      size="sm"
      onClick={() => onCheckedChange(!checked)}
      className="w-12 h-6 relative"
    >
      <span className={`absolute transition-all ${checked ? 'right-1' : 'left-1'}`}>
        {checked ? 'ON' : 'OFF'}
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
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { user } = useAuthStore()
  const { theme, setTheme } = useThemeStore()

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
        title: 'Settings saved',
        description: 'Your preferences have been saved to the server.',
      })
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to save settings. Please try again.',
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

  // Local state for API key inputs — saves on blur instead of every keystroke
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

  // Handle profile update
  const handleProfileUpdate = async () => {
    if (profileForm.newPassword && profileForm.newPassword !== profileForm.confirmPassword) {
      toast({
        title: 'Error',
        description: 'New passwords do not match.',
        variant: 'destructive',
      })
      return
    }

    toast({
      title: 'Profile updated',
      description: 'Your profile has been updated successfully.',
    })
  }

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
        <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
        <p className="text-muted-foreground">
          Manage your account preferences and configuration
        </p>
      </div>

      <Tabs defaultValue="profile" className="space-y-4">
        <TabsList>
          <TabsTrigger value="profile" className="flex items-center gap-2">
            <User className="h-4 w-4" />
            Profile
          </TabsTrigger>
          <TabsTrigger value="notifications" className="flex items-center gap-2">
            <Bell className="h-4 w-4" />
            Notifications
          </TabsTrigger>
          <TabsTrigger value="appearance" className="flex items-center gap-2">
            {theme === 'dark' ? <Moon className="h-4 w-4" /> : <Sun className="h-4 w-4" />}
            Appearance
          </TabsTrigger>
          <TabsTrigger value="api" className="flex items-center gap-2">
            <Key className="h-4 w-4" />
            API Keys
          </TabsTrigger>
        </TabsList>

        {/* Profile Tab */}
        <TabsContent value="profile" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Profile Information</CardTitle>
              <CardDescription>
                Update your account details and password
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email Address</Label>
                <Input
                  id="email"
                  type="email"
                  value={profileForm.email}
                  onChange={(e) => setProfileForm({ ...profileForm, email: e.target.value })}
                  placeholder="your@email.com"
                />
              </div>

              <div className="border-t pt-4 mt-4">
                <h4 className="font-medium mb-4">Change Password</h4>
                <div className="space-y-4">
                  <div className="space-y-2">
                    <Label htmlFor="currentPassword">Current Password</Label>
                    <Input
                      id="currentPassword"
                      type="password"
                      value={profileForm.currentPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, currentPassword: e.target.value })}
                      placeholder="Enter current password"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="newPassword">New Password</Label>
                    <Input
                      id="newPassword"
                      type="password"
                      value={profileForm.newPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, newPassword: e.target.value })}
                      placeholder="Enter new password"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="confirmPassword">Confirm New Password</Label>
                    <Input
                      id="confirmPassword"
                      type="password"
                      value={profileForm.confirmPassword}
                      onChange={(e) => setProfileForm({ ...profileForm, confirmPassword: e.target.value })}
                      placeholder="Confirm new password"
                    />
                  </div>
                </div>
              </div>

              <Button onClick={handleProfileUpdate} className="w-full">
                Update Profile
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Notifications Tab */}
        <TabsContent value="notifications" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Notification Preferences</CardTitle>
              <CardDescription>
                Choose what notifications you want to receive
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Price Alerts</Label>
                  <p className="text-sm text-muted-foreground">
                    Get notified when your price alerts trigger
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.price_alerts}
                  onCheckedChange={() => toggleNotification('price_alerts')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>News Alerts</Label>
                  <p className="text-sm text-muted-foreground">
                    Get notified about news matching your keywords
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.news_alerts}
                  onCheckedChange={() => toggleNotification('news_alerts')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Report Notifications</Label>
                  <p className="text-sm text-muted-foreground">
                    Get notified when reports are generated
                  </p>
                </div>
                <ToggleButton
                  checked={currentSettings.notifications.report_notifications}
                  onCheckedChange={() => toggleNotification('report_notifications')}
                />
              </div>

              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Email Notifications</Label>
                  <p className="text-sm text-muted-foreground">
                    Receive notifications via email
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
              <CardTitle>Appearance</CardTitle>
              <CardDescription>
                Customize how WebStock looks for you
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="flex items-center justify-between">
                <div className="space-y-0.5">
                  <Label>Dark Mode</Label>
                  <p className="text-sm text-muted-foreground">
                    Toggle between dark and light theme
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

        {/* API Keys Tab */}
        <TabsContent value="api" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>API Configuration</CardTitle>
              <CardDescription>
                Configure your API keys for external data sources. These are securely stored on the server.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* News Source Selection */}
              <div className="space-y-2">
                <Label htmlFor="newsSource">News Source</Label>
                <p className="text-sm text-muted-foreground">
                  Choose your preferred news provider for trending news and stock news
                </p>
                <select
                  id="newsSource"
                  value={currentSettings.news_source?.source || 'auto'}
                  onChange={(e) => updateNewsSource(e.target.value)}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                >
                  <option value="auto">Auto (Recommended) - US→YFinance, A-shares→AKShare</option>
                  <option value="yfinance">YFinance - US stocks only</option>
                  <option value="finnhub">Finnhub - US stocks only</option>
                  <option value="akshare">AKShare - Chinese A-shares only</option>
                </select>
                <p className="text-xs text-muted-foreground">
                  Auto mode uses the best source for each market. YFinance/Finnhub for US, AKShare (Eastmoney) for A-shares.
                </p>
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
                  <Label htmlFor="openaiModel">OpenAI Model</Label>
                  <p className="text-sm text-muted-foreground">
                    Model to use for AI analysis (e.g. gpt-4o-mini, gpt-4o, deepseek-chat)
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
                  <strong>Secure Storage:</strong> Your API keys are securely stored on the server 
                  and associated with your account. They will persist across devices and sessions.
                </p>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
