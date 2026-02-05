import { useState } from 'react'
import { Link } from 'react-router-dom'
import { LineChart, Eye, EyeOff, Check, X, Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useAuthStore } from '@/stores/authStore'
import { useLocale } from '@/hooks'
import { isValidEmail, validatePassword, cn } from '@/lib/utils'
import type { Locale } from '@/hooks'

export default function RegisterPage() {
  const { t } = useTranslation('auth')
  const { t: tCommon } = useTranslation('common')
  const { locale, setLocale } = useLocale()
  const { register, isLoading, error, clearError } = useAuthStore()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [validationError, setValidationError] = useState('')

  const passwordValidation = validatePassword(password)
  const passwordsMatch = password === confirmPassword && password.length > 0

  const passwordRequirements = [
    { labelKey: 'password.minLength' as const, met: password.length >= 8 },
    { labelKey: 'password.uppercase' as const, met: /[A-Z]/.test(password) },
    { labelKey: 'password.lowercase' as const, met: /[a-z]/.test(password) },
    { labelKey: 'password.number' as const, met: /[0-9]/.test(password) },
  ]

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setValidationError('')
    clearError()

    if (!email || !password || !confirmPassword) {
      setValidationError(t('validation.fillAllFields'))
      return
    }

    if (!isValidEmail(email)) {
      setValidationError(t('validation.invalidEmail'))
      return
    }

    if (!passwordValidation.isValid) {
      setValidationError(t('validation.invalidPassword'))
      return
    }

    if (password !== confirmPassword) {
      setValidationError(t('password.mismatch'))
      return
    }

    await register(email, password)
  }

  const languageOptions: { value: Locale; label: string }[] = [
    { value: 'en', label: tCommon('language.en') },
    { value: 'zh', label: tCommon('language.zh') },
  ]

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      {/* Language Switcher */}
      <div className="absolute top-4 right-4">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon">
              <Globe className="h-5 w-5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {languageOptions.map((option) => (
              <DropdownMenuItem
                key={option.value}
                onClick={() => setLocale(option.value)}
                className={cn(locale === option.value && 'bg-accent')}
              >
                {option.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Card className="w-full max-w-md">
        <CardHeader className="space-y-1 text-center">
          <div className="flex justify-center mb-4">
            <div className="flex items-center gap-2">
              <LineChart className="h-8 w-8 text-primary" />
              <span className="text-2xl font-bold">WebStock</span>
            </div>
          </div>
          <CardTitle className="text-2xl">{t('register.title')}</CardTitle>
          <CardDescription>
            {t('register.subtitle')}
          </CardDescription>
        </CardHeader>
        <form onSubmit={handleSubmit}>
          <CardContent className="space-y-4">
            {(error || validationError) && (
              <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
                {error || validationError}
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="email">{t('register.email')}</Label>
              <Input
                id="email"
                type="email"
                placeholder={t('register.emailPlaceholder')}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isLoading}
                autoComplete="email"
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="password">{t('register.password')}</Label>
              <div className="relative">
                <Input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  placeholder={t('register.passwordPlaceholder')}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={isLoading}
                  autoComplete="new-password"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                  onClick={() => setShowPassword(!showPassword)}
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <Eye className="h-4 w-4 text-muted-foreground" />
                  )}
                </Button>
              </div>

              {/* Password requirements */}
              {password.length > 0 && (
                <div className="mt-2 space-y-1">
                  {passwordRequirements.map((req) => (
                    <div
                      key={req.labelKey}
                      className={cn(
                        'flex items-center gap-2 text-xs',
                        req.met ? 'text-stock-up' : 'text-muted-foreground'
                      )}
                    >
                      {req.met ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
                      {t(req.labelKey)}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-2">
              <Label htmlFor="confirmPassword">{t('register.confirmPassword')}</Label>
              <Input
                id="confirmPassword"
                type={showPassword ? 'text' : 'password'}
                placeholder={t('register.confirmPasswordPlaceholder')}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                disabled={isLoading}
                autoComplete="new-password"
              />
              {confirmPassword.length > 0 && (
                <div
                  className={cn(
                    'flex items-center gap-2 text-xs',
                    passwordsMatch ? 'text-stock-up' : 'text-destructive'
                  )}
                >
                  {passwordsMatch ? <Check className="h-3 w-3" /> : <X className="h-3 w-3" />}
                  {passwordsMatch ? t('password.match') : t('password.mismatch')}
                </div>
              )}
            </div>
          </CardContent>
          <CardFooter className="flex flex-col space-y-4">
            <Button
              type="submit"
              className="w-full"
              disabled={isLoading || !passwordValidation.isValid || !passwordsMatch}
            >
              {isLoading ? (
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : (
                t('register.submit')
              )}
            </Button>
            <p className="text-center text-sm text-muted-foreground">
              {t('register.hasAccount')}{' '}
              <Link to="/login" className="text-primary hover:underline">
                {t('register.signIn')}
              </Link>
            </p>
          </CardFooter>
        </form>
      </Card>
    </div>
  )
}
